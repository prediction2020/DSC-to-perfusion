import numpy as np
import torch
import matplotlib

import torch.nn as nn
import torch.autograd as autograd
import torch.nn.functional as F
from torch.utils.data import TensorDataset, DataLoader
import torch.optim as optim
import matplotlib.pyplot as plt
import nibabel as nib
from nibabel import processing

import config as c
from model import UnetGenerator, TmpAndUnet
matplotlib.use("Agg")


def mae(actual: np.ndarray, predicted: np.ndarray):
    return np.mean(np.abs((actual - predicted)))


def save_figs(epoch, input_img_plt, target_img_plt, gen_img_plt, 
              losses_g, losses_d, losses_val, gen_img_path, threeD=False):
    if threeD:
        plt.figure()
        plt.subplot(1,3,2)
        plt.imshow(gen_img_plt)
        plt.title("Generated")
        plt.axis("off")
        plt.subplot(1,3,3)
        plt.imshow(target_img_plt)
        plt.title("Target")
        plt.axis("off")
        plt.subplot(1,3,1)
        plt.imshow(input_img_plt)
        plt.axis("off")
        plt.title("Input")
        plt.savefig(gen_img_path + str(epoch) + ".png")
    else:
        plt.figure()
        for i in range(c.nr_imgs_gen):
            plt.subplot(c.nr_imgs_gen, 4, 1+4*i)
            if i==0:
                plt.title("Input")
            plt.imshow(input_img_plt[i, 0, :, :], cmap="rainbow")
            plt.axis("off")
            plt.subplot(c.nr_imgs_gen, 4, 2+4*i)
            if i==0:
                plt.title("Generated")
            plt.imshow(gen_img_plt[i, 0, :, :], cmap="rainbow")
            plt.axis("off")
            plt.subplot(c.nr_imgs_gen, 4, 3+4*i)
            if i==0:
                plt.title("Target")
            plt.imshow(target_img_plt[i, 0, :, :], cmap="rainbow")
            plt.axis("off")
            plt.subplot(c.nr_imgs_gen, 4, 4+4*i)
            if i==0:
                plt.title("Error")
            err_map = np.abs(target_img_plt[i, 0, :, :] - gen_img_plt[i, 0, :, :])
            plt.imshow(err_map, cmap="rainbow")
            plt.axis("off")
        plt.savefig(gen_img_path + str(epoch) + ".png")
        plt.close()

    # save losses
    plt.figure()
    plt.subplot(3, 1, 1)
    plt.plot(range(1, len(losses_d) + 1), losses_d)
    plt.title('Discriminator')
    plt.ylabel('loss')
    plt.xlabel('epoch')
    plt.subplot(3, 1, 2)
    plt.plot(range(1, len(losses_g) + 1), losses_g)
    plt.title('Generator')
    plt.ylabel('loss')
    plt.xlabel('epoch')  
    plt.subplot(3, 1, 3)      
    plt.plot(range(1, len(losses_val) + 1), losses_val)
    plt.title('Validation loss')
    plt.ylabel('loss')
    plt.xlabel('epoch')
    plt.savefig(gen_img_path + "losses.png")
    plt.close()

    np.savez_compressed(gen_img_path + "losses.npz", d=losses_d, g=losses_g, val=losses_val)  


def load_g(trial, epoch, netG_type, num_downs=7, ngf=64, ups=False, normlayer="batchnorm"):
    if normlayer=="batchnorm":
        norm_layer_g_2d = nn.BatchNorm2d
        norm_layer_g_3d = nn.BatchNorm3d
        #norm_layer_g_4d = convNd.BatchNorm4d
    elif normlayer=="instancenorm":
        norm_layer_g_2d = nn.InstanceNorm2d
        norm_layer_g_3d = nn.InstanceNorm3d
        #norm_layer_g_4d = convNd.InstanceNorm4d
        
    if c.use_gpu:
        device = torch.device("cuda:0")
    if netG_type=="tmpandunet":
        net_g = TmpAndUnet(1, 1, ngf=ngf, norm_layer_2d=norm_layer_g_2d, 
                           norm_layer_3d=norm_layer_g_3d, 
                           ups=ups, nr_layer_g=num_downs)
    else:
        net_g = UnetGenerator(80,1,num_downs=num_downs, ngf=ngf, ups=ups,             
                              norm_layer=norm_layer_g_2d,).to(device)

    # load model
    saved_model_path = "{}models/DSC-perf/Trial_{}/epoch{}.pth".format(c.root_path, 
                                                                       trial, epoch)
    saved_params = torch.load(saved_model_path)

    # optimizer
    optim_g = optim.Adam(net_g.parameters(), lr=saved_params["lr_g"], 
                         betas=(saved_params["beta1_g"], saved_params["beta2_g"]),
                         eps=0)

    # initalize weights with saved ones
    net_g.load_state_dict(saved_params["generator_state_dict"])
    optim_g.load_state_dict(saved_params["gen_opt_state_dict"])

    # multiple gpu usage
    if c.use_gpu and (c.nr_gpus > 1):
        net_g = nn.DataParallel(net_g, device_ids=c.gpu_idx)

    return net_g, saved_params


def resize_img(old_img, new_dims):
    if len(old_img.shape)==4:
        new_img_final = np.empty((new_dims[0], new_dims[1], new_dims[2], old_img.shape[3]))
        for i in range(old_img.shape[3]):
            af = old_img.affine
            voxel_size = old_img.header.get_zooms()
            old_img_img = old_img.get_fdata()[:,:,:,i].squeeze()
            old_dims = old_img_img.shape
            zoom = voxel_size[:3] * (1/(np.array(new_dims)/np.array(old_dims)))
            n_affine = nib.affines.rescale_affine(affine=af, shape=old_dims, zooms=zoom, new_shape=new_dims)
            ex_img = nib.Nifti1Image(np.ones(new_dims,dtype=np.int16), n_affine)
            old_img_nii = nib.Nifti1Image(old_img_img, af)
            new_img = processing.resample_from_to(old_img_nii, ex_img)      
            new_img_final[:,:,:,i] =  new_img.get_fdata()
        return nib.Nifti1Image(new_img_final, n_affine)
    elif len(old_img.shape)==3:
        af = old_img.affine
        voxel_size = old_img.header.get_zooms()
        old_dims = old_img.shape
        zoom = voxel_size * (1/(np.array(new_dims)/np.array(old_dims)))
        n_affine = nib.affines.rescale_affine(affine=af, shape=old_dims, zooms=zoom, new_shape=new_dims)
        ex_img = nib.Nifti1Image(np.ones(new_dims,dtype=np.int16), n_affine)
        return processing.resample_from_to(old_img, ex_img)
    
