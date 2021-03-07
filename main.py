import torch
import numpy as np
from models import unetplusplus
import matplotlib
from modules_core import dummy_loader
import argparse
from skimage import color
from tqdm import tqdm
import time
import matplotlib.pyplot as plt
from modules import tensorboard_utils
import os
os.environ['KMP_DUPLICATE_LIB_OK']='True'
matplotlib.use('TkAgg')
import torch.utils.data

parser = argparse.ArgumentParser(description='Model trainer')
parser.add_argument('-run_name', default=f'run_{time.time()}', type=str)
parser.add_argument('-sequence_name', default=f'../../seq_default', type=str)
parser.add_argument('-is_cuda', default=True, type=lambda x: (str(x).lower() == 'true'))
parser.add_argument('-learning_rate', default=1e-4, type=float)
parser.add_argument('-batch_size', default=1, type=int)
parser.add_argument('-epochs', default=1000, type=int)
parser.add_argument('-is_deep_supervision', default=True, type=bool)
parser.add_argument('-unet_depth', default=5, type=int)
parser.add_argument('-first_conv_channel_count', default=8, type=int)
parser.add_argument('-expansion_rate', default=3, type=int)
# TODO add more params and make more beautitfull cuz this file is a mess
args = parser.parse_args()

summary_writer = tensorboard_utils.CustomSummaryWriter(
    log_dir=f'{args.sequence_name}/{args.run_name}'
)


USE_CUDA = torch.cuda.is_available()
MAX_LEN = 200 # limit max number of samples otherwise too slow training (on GPU use all samples / for final training)
if USE_CUDA:
    MAX_LEN = None

dataset = dummy_loader.SyntheticNoiseDataset(augmented_directory=r'C:\Users\37120\Documents\BachelorThesis\image_data\flick30k_10_augmented0',
                                             greyscale_directory=r'C:\Users\37120\Documents\BachelorThesis\image_data\flick30k_10_test')
train_size = int(0.8 * len(dataset))
print(len(dataset))
test_size = len(dataset) - train_size
dataset_train, dataset_test = torch.utils.data.random_split(dataset, [train_size, test_size])

data_loader_train = torch.utils.data.DataLoader(
    dataset=dataset_train,
    batch_size=args.batch_size,
)
data_loader_test = torch.utils.data.DataLoader(
    dataset=dataset_test,
    batch_size=args.batch_size,
)

#for data_loader in [data_loader_train, data_loader_test]:
#    stage = 'train'
#    if data_loader == data_loader_test:
#        stage = 'test'
#    for sample in tqdm(data_loader):
#        y = sample['greyscale_image']
#        x = sample['augmented_image']
#
#        x_img = np.zeros(shape=(320,480,3))
#        y_img = np.zeros(shape=(320, 480, 3))
#
#        y_img[:,:,0] = y[0,0,:,:]
#
#        x_img[:,:,0] = x[0, 0, :, :]
#        print(y_img.shape)
#        print(y_img)
#        y_img = y_img * 100
#        x_img = x_img * 100
#        image1 = color.lab2rgb(y_img)
#        image2 = color.lab2rgb(x_img)
#
#        plt.title(f'{stage}')
#        plt.imshow(image2)
#        plt.show()

model = unetplusplus.Model(args)

loss_func = torch.nn.MSELoss()
optimizer = torch.optim.Adam(model.parameters(), lr=args.learning_rate)

if USE_CUDA:
    model = model.cuda()
    loss_func = loss_func.cuda()

metrics = {}
for stage in ['train', 'test']:
    for metric in [
        'loss',
    ]:
        metrics[f'{stage}_{metric}'] = []


for epoch in range(1, args.epochs):

    for data_loader in [data_loader_train, data_loader_test]:
        metrics_epoch = {key: [] for key in metrics.keys()}

        stage = 'train'
        if data_loader == data_loader_test:
            stage = 'test'

        for sample in tqdm(data_loader):
            y = sample['greyscale_image']
            x = sample['augmented_image']

            y = y.float()
            x = x.float()
            if USE_CUDA:
                x = x.cuda()
                y = y.cuda()

            y_prim = model.forward(x)
            loss = loss_func.forward(y_prim, y)
            metrics_epoch[f'{stage}_loss'].append(loss.item()) # Tensor(0.1) => 0.1f

            summary_writer.add_hparams(
                hparam_dict=args.__dict__,
                    metric_dict={
                    'test_loss':loss.item(),
                }
            )

            summary_writer.add_scalar(
                tag=f'{data_loader}_loss',
                scalar_value=loss.item(),
                global_step=epoch
            )

            if data_loader == data_loader_train:
                summary_writer.add_hparams(
                    hparam_dict=args.__dict__,
                    metric_dict={
                        'train_loss': loss.item()
                    }
                )

            summary_writer.flush()

            if data_loader == data_loader_train:
                loss.backward()
                optimizer.step()
                optimizer.zero_grad()

            loss = loss.cpu()
            y_prim = y_prim.cpu()
            x = x.cpu()
            y = y.cpu()

            np_y_prim = y_prim.data.numpy()


        metrics_strs = []
        for key in metrics_epoch.keys():
            if stage in key:
                value = np.mean(metrics_epoch[key])
                metrics[key].append(value)
                metrics_strs.append(f'{key}: {round(value, 2)}')

        plt.clf()
        plts=[]
        c = 0
        for key, value in metrics.items():
            plts += plt.plot(value, f'C{c}', label=key)
            c += 1
        plt.legend(plts, [it.get_label() for it in plts])
        plt.draw()
        plt.pause(0.1)

        summary_writer.flush()

        print(f'epoch: {epoch} {" ".join(metrics_strs)}')


summary_writer.close()
input('quit?')

# TODO - Logging at the end of epoch