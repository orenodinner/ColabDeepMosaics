import os
import numpy as np
import cv2
import random
import torch
import torch.nn as nn
import time

import sys
sys.path.append("..")
sys.path.append("../..")
from util import mosaic,util,ffmpeg,filt,data
from util import image_processing as impro
from cores import Options
from models import pix2pix_model,pix2pixHD_model,video_model,unet_model,loadmodel,videoHD_model
from matplotlib import pyplot as plt
import torch.backends.cudnn as cudnn

'''
--------------------------Get options--------------------------
'''

opt = Options()
opt.parser.add_argument('--gpu_id',type=int,default=0, help='')
opt.parser.add_argument('--N',type=int,default=25, help='')
opt.parser.add_argument('--lr',type=float,default=0.0002, help='')
opt.parser.add_argument('--beta1',type=float,default=0.5, help='')
opt.parser.add_argument('--gan', action='store_true', help='if specified, use gan')
opt.parser.add_argument('--l2', action='store_true', help='if specified, use L2 loss')
opt.parser.add_argument('--hd', action='store_true', help='if specified, use HD model')
opt.parser.add_argument('--lambda_L1',type=float,default=100, help='')
opt.parser.add_argument('--lambda_gan',type=float,default=1, help='')
opt.parser.add_argument('--finesize',type=int,default=256, help='')
opt.parser.add_argument('--loadsize',type=int,default=286, help='')
opt.parser.add_argument('--batchsize',type=int,default=1, help='')
opt.parser.add_argument('--perload_num',type=int,default=16, help='number of images pool')
opt.parser.add_argument('--norm',type=str,default='instance', help='')

opt.parser.add_argument('--dataset',type=str,default='./datasets/face/', help='')
opt.parser.add_argument('--maxiter',type=int,default=10000000, help='')
opt.parser.add_argument('--savefreq',type=int,default=10000, help='')
opt.parser.add_argument('--startiter',type=int,default=0, help='')
opt.parser.add_argument('--continuetrain', action='store_true', help='')
opt.parser.add_argument('--savename',type=str,default='face', help='')


'''
--------------------------Init--------------------------
'''
opt = opt.getparse()
dir_checkpoint = os.path.join('checkpoints/',opt.savename)
util.makedirs(dir_checkpoint)
util.writelog(os.path.join(dir_checkpoint,'loss.txt'), 
              str(time.asctime(time.localtime(time.time())))+'\n'+util.opt2str(opt))
torch.cuda.set_device(opt.gpu_id)

N = opt.N
loss_sum = [0.,0.,0.,0.]
loss_plot = [[],[]]
item_plot = []

# list video dir 
videonames = os.listdir(opt.dataset)
videonames.sort()
lengths = [];tmp = []
print('Check dataset...')
for video in videonames:
    if video != 'opt.txt':
        video_images = os.listdir(os.path.join(opt.dataset,video,'origin_image'))
        lengths.append(len(video_images))
        tmp.append(video)
videonames = tmp
video_num = len(videonames)
#def network
print('Init network...')
if opt.hd:
    netG = videoHD_model.MosaicNet(3*N+1, 3, norm=opt.norm)
else:
    netG = video_model.MosaicNet(3*N+1, 3, norm=opt.norm)
loadmodel.show_paramsnumber(netG,'netG')

if opt.gan:
    if opt.hd:
        #netD = pix2pixHD_model.define_D(6, 64, 3, norm = opt.norm, use_sigmoid=False, num_D=1)
        netD = pix2pixHD_model.define_D(6, 64, 3, norm = opt.norm, use_sigmoid=False, num_D=2,getIntermFeat=True)    
    else:
        netD = pix2pix_model.define_D(3*2, 64, 'basic', norm = opt.norm)
    netD.train()

if opt.continuetrain:
    if not os.path.isfile(os.path.join(dir_checkpoint,'last_G.pth')):
        opt.continuetrain = False
        print('can not load last_G, training on init weight.')
if opt.continuetrain:     
    netG.load_state_dict(torch.load(os.path.join(dir_checkpoint,'last_G.pth')))
    if opt.gan:
        netD.load_state_dict(torch.load(os.path.join(dir_checkpoint,'last_D.pth')))
    f = open(os.path.join(dir_checkpoint,'iter'),'r')
    opt.startiter = int(f.read())
    f.close()

optimizer_G = torch.optim.Adam(netG.parameters(), lr=opt.lr,betas=(opt.beta1, 0.999))
criterion_L1 = nn.L1Loss()
criterion_L2 = nn.MSELoss()
if opt.gan:
    optimizer_D = torch.optim.Adam(netD.parameters(), lr=opt.lr,betas=(opt.beta1, 0.999))
    if opt.hd:
        criterionGAN = pix2pixHD_model.GANLoss(tensor=torch.cuda.FloatTensor)
    else:
        criterionGAN = pix2pix_model.GANLoss(gan_mode='lsgan').cuda()   

if opt.use_gpu:
    netG.cuda()
    if opt.gan:
        netD.cuda()
        criterionGAN.cuda()
    cudnn.benchmark = True

'''
--------------------------preload data & data pool--------------------------
'''
def loaddata(video_index):
    
    videoname = videonames[video_index]
    img_index = random.randint(int(N/2)+1,lengths[video_index]- int(N/2)-1)
    
    input_img = np.zeros((opt.loadsize,opt.loadsize,3*N+1), dtype='uint8')
    # this frame
    this_mask = impro.imread(os.path.join(opt.dataset,videoname,'mask','%05d'%(img_index)+'.png'),'gray',loadsize=opt.loadsize)
    input_img[:,:,-1] = this_mask
    #print(os.path.join(opt.dataset,videoname,'origin_image','%05d'%(img_index)+'.jpg'))
    ground_true =  impro.imread(os.path.join(opt.dataset,videoname,'origin_image','%05d'%(img_index)+'.jpg'),loadsize=opt.loadsize)
    mosaic_size,mod,rect_rat,father = mosaic.get_random_parameter(ground_true,this_mask)
    # merge other frame
    for i in range(0,N):
        img =  impro.imread(os.path.join(opt.dataset,videoname,'origin_image','%05d'%(img_index+i-int(N/2))+'.jpg'),loadsize=opt.loadsize)
        mask = impro.imread(os.path.join(opt.dataset,videoname,'mask','%05d'%(img_index+i-int(N/2))+'.png'),'gray',loadsize=opt.loadsize)
        img_mosaic = mosaic.addmosaic_base(img, mask, mosaic_size,model = mod,rect_rat=rect_rat,father=father)
        input_img[:,:,i*3:(i+1)*3] = img_mosaic
    # to tensor
    input_img,ground_true = data.random_transform_video(input_img,ground_true,opt.finesize,N)
    input_img = data.im2tensor(input_img,bgr2rgb=False,use_gpu=opt.use_gpu,use_transform = False,is0_1=False)
    ground_true = data.im2tensor(ground_true,bgr2rgb=False,use_gpu=opt.use_gpu,use_transform = False,is0_1=False)
    
    return input_img,ground_true

print('Preloading data, please wait...')

if opt.perload_num <= opt.batchsize:
    opt.perload_num = opt.batchsize*2
#data pool
input_imgs = torch.rand(opt.perload_num,N*3+1,opt.finesize,opt.finesize).cuda()
ground_trues = torch.rand(opt.perload_num,3,opt.finesize,opt.finesize).cuda()
load_cnt = 0

def preload():
    global load_cnt   
    while 1:
        try:
            video_index = random.randint(0,video_num-1)
            ran = random.randint(0, opt.perload_num-1)
            input_imgs[ran],ground_trues[ran] = loaddata(video_index)
            load_cnt += 1
            # time.sleep(0.1)
        except Exception as e:
            print("error:",e)
import threading
t = threading.Thread(target=preload,args=()) 
t.daemon = True
t.start()
time_start=time.time()
while load_cnt < opt.perload_num:
    time.sleep(0.1)
time_end=time.time()
print('load speed:',round((time_end-time_start)/opt.perload_num,3),'s/it')

'''
--------------------------train--------------------------
'''
util.copyfile('./train.py', os.path.join(dir_checkpoint,'train.py'))
util.copyfile('../../models/videoHD_model.py', os.path.join(dir_checkpoint,'model.py'))
netG.train()
time_start=time.time()
print("Begin training...")
for iter in range(opt.startiter+1,opt.maxiter):

    ran = random.randint(0, opt.perload_num-opt.batchsize-1)
    inputdata = input_imgs[ran:ran+opt.batchsize].clone()
    target = ground_trues[ran:ran+opt.batchsize].clone()

    if opt.gan:
        # compute fake images: G(A)
        pred = netG(inputdata)
        # update D
        pix2pix_model.set_requires_grad(netD,True)
        optimizer_D.zero_grad()
        # Fake
        real_A = inputdata[:,int((N-1)/2)*3:(int((N-1)/2)+1)*3,:,:]
        fake_AB = torch.cat((real_A, pred), 1)
        pred_fake = netD(fake_AB.detach())
        loss_D_fake = criterionGAN(pred_fake, False)
        # Real
        real_AB = torch.cat((real_A, target), 1)
        pred_real = netD(real_AB)
        loss_D_real = criterionGAN(pred_real, True)
        # combine loss and calculate gradients
        loss_D = (loss_D_fake + loss_D_real) * 0.5
        loss_sum[2] += loss_D_fake.item()
        loss_sum[3] += loss_D_real.item()
        # udpate D's weights
        loss_D.backward()
        optimizer_D.step()

        # update G
        pix2pix_model.set_requires_grad(netD,False)
        optimizer_G.zero_grad()
        # First, G(A) should fake the discriminator
        real_A = inputdata[:,int((N-1)/2)*3:(int((N-1)/2)+1)*3,:,:]
        fake_AB = torch.cat((real_A, pred), 1)
        pred_fake = netD(fake_AB)
        loss_G_GAN = criterionGAN(pred_fake, True)*opt.lambda_gan
        # Second, G(A) = B
        if opt.l2:
            loss_G_L1 = (criterion_L1(pred, target)+criterion_L2(pred, target)) * opt.lambda_L1
        else:
            loss_G_L1 = criterion_L1(pred, target) * opt.lambda_L1
        # combine loss and calculate gradients
        loss_G = loss_G_GAN + loss_G_L1
        loss_sum[0] += loss_G_L1.item()
        loss_sum[1] += loss_G_GAN.item()
        # udpate G's weights
        loss_G.backward()
        optimizer_G.step()

    else:
        pred = netG(inputdata)
        if opt.l2:
            loss_G_L1 = (criterion_L1(pred, target)+criterion_L2(pred, target)) * opt.lambda_L1
        else:
            loss_G_L1 = criterion_L1(pred, target) * opt.lambda_L1
        loss_sum[0] += loss_G_L1.item()

        optimizer_G.zero_grad()
        loss_G_L1.backward()
        optimizer_G.step()

    if (iter+1)%100 == 0:
        try:
            data.showresult(inputdata[:,int((N-1)/2)*3:(int((N-1)/2)+1)*3,:,:],
             target, pred,os.path.join(dir_checkpoint,'result_train.jpg'))
        except Exception as e:
            print(e)
    # plot
    if (iter+1)%1000 == 0:
        time_end = time.time()
        if opt.gan:
            savestr ='iter:{0:d} L1_loss:{1:.4f} G_loss:{2:.4f} D_f:{3:.4f} D_r:{4:.4f} time:{5:.2f}'.format(
                iter+1,loss_sum[0]/1000,loss_sum[1]/1000,loss_sum[2]/1000,loss_sum[3]/1000,(time_end-time_start)/1000)
            util.writelog(os.path.join(dir_checkpoint,'loss.txt'), savestr,True)
            if (iter+1)/1000 >= 10:
                loss_plot[0].append(loss_sum[0]/1000)
                loss_plot[1].append(loss_sum[1]/1000)
                item_plot.append(iter+1)
                try:
                    plt.plot(item_plot,loss_plot[0])
                    plt.plot(item_plot,loss_plot[1])
                    plt.savefig(os.path.join(dir_checkpoint,'loss.jpg'))
                    plt.close()
                except Exception as e:
                    print("error:",e)
        else:
            savestr ='iter:{0:d}  L1_loss:{1:.4f}  time:{2:.2f}'.format(iter+1,loss_sum[0]/1000,(time_end-time_start)/1000)
            util.writelog(os.path.join(dir_checkpoint,'loss.txt'), savestr,True)
            if (iter+1)/1000 >= 10:
                loss_plot[0].append(loss_sum[0]/1000)
                item_plot.append(iter+1)
                try:
                    plt.plot(item_plot,loss_plot[0])
                    plt.savefig(os.path.join(dir_checkpoint,'loss.jpg'))
                    plt.close()
                except Exception as e:
                    print("error:",e)
        loss_sum = [0.,0.,0.,0.]
        time_start=time.time()

    # save network
    if (iter+1)%opt.savefreq == 0:
        if iter+1 != opt.savefreq:
            os.rename(os.path.join(dir_checkpoint,'last_G.pth'),os.path.join(dir_checkpoint,str(iter+1-opt.savefreq)+'G.pth'))
        torch.save(netG.cpu().state_dict(),os.path.join(dir_checkpoint,'last_G.pth'))
        if opt.gan:
            if iter+1 != opt.savefreq:
                os.rename(os.path.join(dir_checkpoint,'last_D.pth'),os.path.join(dir_checkpoint,str(iter+1-opt.savefreq)+'D.pth'))
            torch.save(netD.cpu().state_dict(),os.path.join(dir_checkpoint,'last_D.pth'))
        if opt.use_gpu:
            netG.cuda()
            if opt.gan:
                netD.cuda()
        f = open(os.path.join(dir_checkpoint,'iter'),'w+')
        f.write(str(iter+1))
        f.close()
        print('network saved.')

        #test
        if os.path.isdir('./test'):  
            netG.eval()
            
            test_names = os.listdir('./test')
            test_names.sort()
            result = np.zeros((opt.finesize*2,opt.finesize*len(test_names),3), dtype='uint8')

            for cnt,test_name in enumerate(test_names,0):
                img_names = os.listdir(os.path.join('./test',test_name,'image'))
                img_names.sort()
                inputdata = np.zeros((opt.finesize,opt.finesize,3*N+1), dtype='uint8')
                for i in range(0,N):
                    img = impro.imread(os.path.join('./test',test_name,'image',img_names[i]))
                    img = impro.resize(img,opt.finesize)
                    inputdata[:,:,i*3:(i+1)*3] = img

                mask = impro.imread(os.path.join('./test',test_name,'mask.png'),'gray')
                mask = impro.resize(mask,opt.finesize)
                mask = impro.mask_threshold(mask,15,128)
                inputdata[:,:,-1] = mask
                result[0:opt.finesize,opt.finesize*cnt:opt.finesize*(cnt+1),:] = inputdata[:,:,int((N-1)/2)*3:(int((N-1)/2)+1)*3]
                inputdata = data.im2tensor(inputdata,bgr2rgb=False,use_gpu=opt.use_gpu,use_transform = False,is0_1 = False)
                pred = netG(inputdata)
     
                pred = data.tensor2im(pred,rgb2bgr = False, is0_1 = False)
                result[opt.finesize:opt.finesize*2,opt.finesize*cnt:opt.finesize*(cnt+1),:] = pred

            cv2.imwrite(os.path.join(dir_checkpoint,str(iter+1)+'_test.jpg'), result)
            netG.train()
