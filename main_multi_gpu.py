
import os
os.environ['KMP_DUPLICATE_LIB_OK']='True'

import torch
import numpy as np
import matplotlib
import logging
import torch.nn as nn
import argparse
from skimage import color
from torch.utils.tensorboard import SummaryWriter
from tqdm import tqdm
import torchvision
import time
from datetime import datetime
import torchnet as tnt
matplotlib.use('TkAgg')
import torch.utils.data

from modules.file_utils import FileUtils
from modules import tensorboard_utils
from modules import radam
from modules import logging_utils
from modules_core import conv3d_dataloader
from modules import loss_functions
from models import unetplusplus
from models import temporal_unet_plus_pus
from modules.csv_utils_2 import CsvUtils2
from models import DrunkUNET

def main():
    parser = argparse.ArgumentParser(description='Model trainer')
    parser.add_argument('-run_name', default=f'run_{time.time()}', type=str)
    parser.add_argument('-sequence_name', default=f'temporal_unet_memmap2', type=str)
    parser.add_argument('-is_cuda', default=True, type=lambda x: (str(x).lower() == 'true'))
    parser.add_argument('-learning_rate', default=3e-4, type=float)
    parser.add_argument('-batch_size', default=10, type=int)
    parser.add_argument('-path_train', default=[r'/mnt/beegfs2/home/leo01/image_data/video_framed_memmap_dataset/train/'], nargs='*')
    parser.add_argument('-path_test', default=[r'/mnt/beegfs2/home/leo01/image_data/video_framed_memmap_dataset/test/'], nargs='*')
    parser.add_argument('-data_workers', default=1, type=int)
    parser.add_argument('-epochs', default=50, type=int)
    parser.add_argument('-is_deep_supervision', default=True, type=lambda x: (str(x).lower() == 'true'))
    parser.add_argument('-is_debug', default=False, type=lambda x: (str(x).lower() == 'true'))
    parser.add_argument('-unet_depth', default=5, type=int)
    parser.add_argument('-first_conv_channel_count', default=6, type=int)
    parser.add_argument('-expansion_rate', default=2, type=int)
    parser.add_argument('-continue_training', default=False, type=lambda x: (str(x).lower() == 'true'))
    parser.add_argument('-conv3d_depth', default=5, type=int) #ammount of pictures in conv3d

    # TODO add more params and make more beautitfull cuz this file is a mess
    args, _ = parser.parse_known_args()

    path_sequence = f'./results/{args.sequence_name}'
    args.run_name += ('-' + datetime.utcnow().strftime(f'%y-%m-%d--%H-%M-%S'))
    path_run = f'./results/{args.sequence_name}/{args.run_name}'
    FileUtils.createDir(path_run)
    FileUtils.writeJSON(f'{path_run}/args.json', vars(args))
    USE_CUDA = torch.cuda.is_available()
    CsvUtils2.create_global(path_sequence)
    CsvUtils2.create_local(path_sequence, args.run_name)

    rootLogger = logging.getLogger()
    logFormatter = logging.Formatter("%(asctime)s [%(process)d] [%(thread)d] [%(levelname)s]  %(message)s")
    rootLogger.level = logging.DEBUG  # level
    base_name = os.path.basename(path_sequence)
    fileHandler = logging.FileHandler(f'{path_run}/log-{base_name}.txt')
    fileHandler.setFormatter(logFormatter)
    rootLogger.addHandler(fileHandler)

    consoleHandler = logging.StreamHandler()
    consoleHandler.setFormatter(logFormatter)
    rootLogger.addHandler(consoleHandler)

    tensorboard_writer = tensorboard_utils.CustomSummaryWriter(log_dir=path_run)
    tensorboard_utilz = tensorboard_utils.TensorBoardUtils(tensorboard_writer)
    # logging_utilz = logging_utils.LoggingUtils(filename=f'{args.sequence_name}/{args.run_name}.txt')
    last_epoch = 0


    MAX_LEN = 200  # limit max number of samples otherwise too slow training (on GPU use all samples / for final training)
    if USE_CUDA:
        MAX_LEN = None
    data_loader_train, data_loader_test = conv3d_dataloader.get_data_loaders(args)
    model = DrunkUNET.Model(args)

    if torch.cuda.device_count() > 1:
        print("Let's use", torch.cuda.device_count(), "GPUs!")
        # dim = 0 [30, xxx] -> [10, ...], [10, ...], [10, ...] on 3 GPUs
        model = nn.DataParallel(model, device_ids=[0])
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    model.to(device)

    loss_func = torch.nn.MSELoss()
    optimizer = radam.RAdam(model.parameters(), lr=args.learning_rate)


    # if(args.continue_training):
    #     checkpoint = torch.load(args.saved_model_path)
    #     model.load_state_dict(checkpoint['model_state_dict'])
    #     optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
    #     last_epoch = checkpoint['epoch']
    #     model.train()

    if USE_CUDA:
        loss_func = loss_func.to(device)
    metrics = {}

    for stage in ['train', 'test']:
        for metric in [
            'loss',
        ]:
            metrics[f'{stage}_{metric}'] = []

    meters = dict(
        train_loss=tnt.meter.AverageValueMeter(),
        test_loss=tnt.meter.AverageValueMeter(),
    )

    state = {
        'epoch' : 0,
        'train_loss': -1.0,
        'test_loss' : -1.0,
        'best_loss': -1.0,
        'epoch_time': -1.0,
        'average_epoch_time': -1.0,
    }

    hP = args.__dict__
    hP['path_train'] = ''
    hP['path_test'] = ''
    time_list = []

    for epoch in range(last_epoch, args.epochs):
        tensorboard_image_idx = 0
        for key in meters.keys():
            meters[key].reset()
        t0 = time.time()
        for data_loader in [data_loader_train, data_loader_test]:
            metrics_epoch = {key: [] for key in metrics.keys()}
            stage = 'train'
            if data_loader == data_loader_test:
                stage = 'test'

            for batch in tqdm(data_loader):

                y = batch['greyscale_image']
                x = batch['augmented_image']

                y = y.float()
                x = x.float()
                if USE_CUDA:
                    x = x.to(device)
                    y = y.to(device)

                if data_loader == data_loader_train:
                    optimizer.zero_grad()
                    model.zero_grad()
                    y_prim = model.forward(x)
                    loss = loss_func.forward(y_prim[:, 0, :, :], y[:,0,2,:,:])
                else:
                    with torch.no_grad():
                        y_prim = model.forward(x)
                        loss = loss_func.forward(y_prim[:, 0, :, :], y[:,0,2,:,:])
                metrics_epoch[f'{stage}_loss'].append(loss.item())  # Tensor(0.1) => 0.1f
                if data_loader == data_loader_train:
                    loss.backward()
                    optimizer.step()
                    optimizer.zero_grad()

                loss = loss.cpu()
                y_prim = y_prim.cpu()
                x = x.cpu()
                y = y.cpu()

                meters[f'{stage}_loss'].add(loss.item())

                if tensorboard_image_idx < 100 and data_loader == data_loader_test:
                    if tensorboard_image_idx < 100:
                        data = torch.cat([y[0,0,2,:,:], x[0,0,2,:,:], y_prim[0,0,:,:]], 1)
                        tensorboard_writer.add_image(f'sample_{tensorboard_image_idx}', data, dataformats='HW', global_step=epoch)
                        tensorboard_image_idx += 1
                    else:
                        break

        state['train_loss'] = meters['train_loss'].value()[0]
        state['test_loss'] = meters['test_loss'].value()[0]
        state['epoch'] = epoch
        state['epoch_time'] = time.time() - t0
        time_list.append(time.time() - t0)
        state['average_epoch_time'] = sum(time_list)/len(time_list)
        if epoch == 0:
            state['best_loss'] = state['test_loss']
        elif state['test_loss'] < state['best_loss']:
            state['best_loss'] = state['test_loss']
            torch.save({
                'epoch': epoch,
                'model_state_dict': model.state_dict(),
                'optimizer_state_dict': optimizer.state_dict(),
                'module_state_dict' : model.module.state_dict(),
            }, os.path.join(path_run, 'best_loss.pt'))



        tensorboard_writer.add_hparams(
            hparam_dict=hP,
            metric_dict=state,
            global_step=epoch,
            name=args.run_name
        )

        CsvUtils2.add_hparams(
            path_sequence=path_sequence,
            run_name=args.run_name,
            args_dict=hP,
            metrics_dict=state,
            global_step=epoch
        )

        percent = epoch / args.epochs
        logging.info(
            f'{round(percent * 100, 2)}% train loss: {round(state["train_loss"], 5)}, '
            f'test loss: {round(state["test_loss"], 5)}')

        torch.save(model.state_dict(), os.path.join(path_run, 'last.pt'))
        # save last model to continue
        torch.save({
            'epoch': epoch,
            'model_state_dict': model.state_dict(),
            'optimizer_state_dict': optimizer.state_dict(),
            'module' : model.module.state_dict(),
        }, os.path.join(path_run, 'last_checkpoint.pt'))
        tensorboard_writer.flush()
    tensorboard_writer.close()
    input('quit?')


if __name__ == '__main__':
    main()