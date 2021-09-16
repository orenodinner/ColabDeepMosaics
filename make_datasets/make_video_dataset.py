import os
import random
import sys
import datetime
import time
import shutil
import threading

import numpy as np
import cv2

sys.path.append("..")
from models import runmodel,loadmodel
import util.image_processing as impro
from util import util,mosaic,data,ffmpeg
from cores import Options

opt = Options()
opt.parser.add_argument('--datadir',type=str,default='your video dir', help='')
opt.parser.add_argument('--savedir',type=str,default='../datasets/video/face', help='')
opt.parser.add_argument('--interval',type=int,default=30, help='interval of split video ')
opt.parser.add_argument('--time',type=int,default=5, help='split video time')
opt.parser.add_argument('--minmaskarea',type=int,default=2000, help='')
opt.parser.add_argument('--quality', type=int ,default= 45,help='minimal quality')
opt.parser.add_argument('--outsize', type=int ,default= 286,help='')
opt.parser.add_argument('--startcnt', type=int ,default= 0,help='')
opt.parser.add_argument('--minsize', type=int ,default= 96,help='minimal roi size')
opt = opt.getparse()


util.makedirs(opt.savedir)
util.writelog(os.path.join(opt.savedir,'opt.txt'), 
              str(time.asctime(time.localtime(time.time())))+'\n'+util.opt2str(opt))

videopaths = util.Traversal(opt.datadir)
videopaths = util.is_videos(videopaths)
random.shuffle(videopaths)

# def network
net = loadmodel.bisenet(opt,'roi')

result_cnt = opt.startcnt
video_cnt = 1
starttime = datetime.datetime.now()
for videopath in videopaths:
    try:
        timestamps=[]
        fps,endtime,height,width = ffmpeg.get_video_infos(videopath)
        for cut_point in range(1,int((endtime-opt.time)/opt.interval)):
            util.clean_tempfiles()
            ffmpeg.video2image(videopath, './tmp/video2image/%05d.'+opt.tempimage_type,fps=1,
                start_time = util.second2stamp(cut_point*opt.interval),last_time = util.second2stamp(opt.time))
            imagepaths = util.Traversal('./tmp/video2image')
            cnt = 0 
            for i in range(opt.time):
                img = impro.imread(imagepaths[i])
                mask = runmodel.get_ROI_position(img,net,opt,keepsize=True)[0]
                if not opt.all_mosaic_area:
                    mask = impro.find_mostlikely_ROI(mask)
                x,y,size,area = impro.boundingSquare(mask,Ex_mul=1)
                if area > opt.minmaskarea and size>opt.minsize and impro.Q_lapulase(img)>opt.quality:
                    cnt +=1
            if cnt == opt.time:
                # print(second)
                timestamps.append(util.second2stamp(cut_point*opt.interval))
        util.writelog(os.path.join(opt.savedir,'opt.txt'),videopath+'\n'+str(timestamps))
        #print(timestamps)

        # util.clean_tempfiles()
        # fps,endtime,height,width = ffmpeg.get_video_infos(videopath)
        # # print(fps,endtime,height,width)
        # ffmpeg.continuous_screenshot(videopath, './tmp/video2image', 1)

        # # find where to cut
        # print('Find where to cut...')
        # timestamps=[]
        # imagepaths = util.Traversal('./tmp/video2image')
        # for second in range(int(endtime)):
        #     if second%opt.interval==0:
        #         cnt = 0 
        #         for i in range(opt.time):
        #             img = impro.imread(imagepaths[second+i])
        #             mask = runmodel.get_ROI_position(img,net,opt)[0]
        #             if not opt.all_mosaic_area:
        #                 mask = impro.find_mostlikely_ROI(mask)
        #             if impro.mask_area(mask) > opt.minmaskarea and impro.Q_lapulase(img)>opt.quality:
        #                 # print(impro.mask_area(mask))
        #                 cnt +=1
        #         if cnt == opt.time:
        #             # print(second)
        #             timestamps.append(util.second2stamp(second))

        #generate datasets
        print('Generate datasets...')
        for timestamp in timestamps:
            savecnt = '%05d' % result_cnt
            origindir = os.path.join(opt.savedir,savecnt,'origin_image')
            maskdir = os.path.join(opt.savedir,savecnt,'mask')
            util.makedirs(origindir)
            util.makedirs(maskdir)

            util.clean_tempfiles()
            ffmpeg.video2image(videopath, './tmp/video2image/%05d.'+opt.tempimage_type,
                start_time = timestamp,last_time = util.second2stamp(opt.time))
            
            endtime = datetime.datetime.now()
            print(str(video_cnt)+'/'+str(len(videopaths))+' ',
                util.get_bar(100*video_cnt/len(videopaths),35),'',
                util.second2stamp((endtime-starttime).seconds)+'/'+util.second2stamp((endtime-starttime).seconds/video_cnt*len(videopaths)))

            imagepaths = util.Traversal('./tmp/video2image')
            imagepaths = sorted(imagepaths)
            imgs=[];masks=[]
            mask_flag = False

            for imagepath in imagepaths:
                img = impro.imread(imagepath)
                mask = runmodel.get_ROI_position(img,net,opt,keepsize=True)[0]
                imgs.append(img)
                masks.append(mask)
                if not mask_flag:
                    mask_avg = mask.astype(np.float64)
                    mask_flag = True
                else:
                    mask_avg += mask.astype(np.float64)

            mask_avg = np.clip(mask_avg/len(imagepaths),0,255).astype('uint8')
            mask_avg = impro.mask_threshold(mask_avg,20,64)
            if not opt.all_mosaic_area:
                mask_avg = impro.find_mostlikely_ROI(mask_avg)
            x,y,size,area = impro.boundingSquare(mask_avg,Ex_mul=random.uniform(1.1,1.5))
            
            for i in range(len(imagepaths)):
                img = impro.resize(imgs[i][y-size:y+size,x-size:x+size],opt.outsize,interpolation=cv2.INTER_CUBIC) 
                mask = impro.resize(masks[i][y-size:y+size,x-size:x+size],opt.outsize,interpolation=cv2.INTER_CUBIC)
                impro.imwrite(os.path.join(origindir,'%05d'%(i+1)+'.jpg'), img)
                impro.imwrite(os.path.join(maskdir,'%05d'%(i+1)+'.png'), mask)

            result_cnt+=1

    except Exception as e:
        video_cnt +=1
        util.writelog(os.path.join(opt.savedir,'opt.txt'), 
              videopath+'\n'+str(result_cnt)+'\n'+str(e))
    video_cnt +=1
