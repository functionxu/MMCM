# -*- encoding: utf-8 -*-
# here put the import lib

import os
import sys
import argparse
import torch
import torch.nn as nn
import torchvision.transforms as transforms
import numpy as np
from PIL import Image
from utils import getLanMask
from utils.config import cfg_from_yaml_file, cfg
from models.vl_model import *
import glob
from tqdm import tqdm
import pickle
import json
import cv2
from torchvision.ops import nms
from bbox_extractor.bbox_extractor import BboxExtractor
import parser
import pandas as pd
import random
import pdb
from img_feat_extractor import generate_folder_csv


class ImgModel(nn.Module):
    def __init__(self, model_cfg):
        super(ImgModel, self).__init__()

        self.model_cfg = model_cfg

        self.learnable = nn.ModuleDict()
        self.learnable['imgencoder'] = ImgLearnableEncoder(model_cfg)

    def forward(self, imgFea, maskImages, image_boxs):
        imgFea = self.learnable['imgencoder'](imgFea, maskImages, image_boxs) # <bsz, img_dim>
        imgFea = F.normalize(imgFea, p=2, dim=-1)
        return imgFea

class ImgFeatureExtractor:
    def __init__(self, cfg_file, model_weights, gpu_id = 0):
        self.gpu_id = gpu_id
        self.cfg_file = cfg_file
        self.cfg = cfg_from_yaml_file(self.cfg_file, cfg)
        self.img_model = ImgModel(model_cfg=self.cfg.MODEL)

        self.img_model = self.img_model.cuda(self.gpu_id)
        model_component = torch.load(model_weights, map_location=torch.device('cuda:{}'.format(self.gpu_id)))
        img_model_component = {}
        for key in model_component["learnable"].keys():
            if "imgencoder." in key:
                img_model_component[key] = model_component["learnable"][key]
        self.img_model.learnable.load_state_dict(img_model_component)
        self.img_model.eval()
        self.visual_transform = self.visual_transforms_box(self.cfg.MODEL.IMG_SIZE)

    def visual_transforms_box(self, new_size = 456):
        mean, std = [0.485, 0.456, 0.406], [0.229, 0.224, 0.225]
        normalize = transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])

        return transforms.Compose([
                transforms.ToTensor(),
                transforms.Resize((new_size, new_size)),
                normalize])

    def extract(self, img_path, bboxes):
        image = Image.open(img_path).convert('RGB')
        if image is None:
            return None
        else:
            width, height = image.size
            new_size = self.cfg.MODEL.IMG_SIZE
            img_box_s = []
            for box_i in bboxes[:self.cfg.MODEL.MAX_IMG_LEN-1]: # [x1, y1, x2, y2]
                x1, y1, x2, y2 = box_i[0] * (new_size/width), box_i[1] * (new_size/height), box_i[2] * (new_size/width), box_i[3] * (new_size/height)
                img_box_s.append(torch.from_numpy(np.array([x1, y1, x2, y2]).astype(np.float32)))     
            img_box_s.append(torch.from_numpy(np.array([0, 0, new_size, new_size]).astype(np.float32)))

            image_boxs = torch.stack(img_box_s, 0) # <36, 4>
            image = self.visual_transform(image)
            img_len = torch.full((1,), self.cfg.MODEL.MAX_IMG_LEN, dtype=torch.long)

            with torch.no_grad():
                imgs = image.unsqueeze(0)  # <batchsize, 3, image_size, image_size>
                img_lens = img_len.unsqueeze(0).view(-1)
                image_boxs = image_boxs.unsqueeze(0) # <BSZ, 36, 4>

                # get image mask
                imgMask = getLanMask(img_lens, cfg.MODEL.MAX_IMG_LEN)
                imgMask = imgMask.cuda(self.gpu_id)

                imgs = imgs.cuda(self.gpu_id)
                image_boxs = image_boxs.cuda(self.gpu_id) # <BSZ, 36, 4>
                img_fea = self.img_model(imgs, imgMask, image_boxs)
                img_fea = img_fea.cpu().numpy()
            return img_fea
        
    

if __name__ == '__main__':
    # python img_feat_extractor.py --frames_dir ./frames --vid_dir /data_share5/douyin/video --vid_csv_path ./vids.csv --feat_save_dir feats
    parser = argparse.ArgumentParser()
    parser.add_argument('--frames_dir', type=str, default=None)
    parser.add_argument('--vid_csv_path', type=str, default=None)
    parser.add_argument('--feat_save_dir', type=str, default=None)
    parser.add_argument('--cfg_file', type=str, default='cfg/test_xyb.yml')
    parser.add_argument('--brivl_checkpoint', type=str, default='/data_share7/sxhong/project/BriVL/weights/BriVL-1.0-5500w.pth')
    parser.add_argument('--bbox_extractor_cfg', type=str, default='bbox_extractor/configs/bua-caffe/extract-bua-caffe-r101.yaml')
    
    args = parser.parse_args()
    abs_path = os.path.dirname(os.path.abspath(__file__))
    cfg_file = os.path.join(abs_path, args.cfg_file)
    model_weights = args.brivl_checkpoint
    
    if not os.path.exists(args.frames_dir):
        os.makedirs(args.frames_dir) 
    if not os.path.exists(args.feat_save_dir):
        os.makedirs(args.feat_save_dir) 
        
    vf_extractor = ImgFeatureExtractor(cfg_file, model_weights)
    bbx_extr = BboxExtractor(os.path.join(abs_path, args.bbox_extractor_cfg))
    # vids = pd.read_csv(args.vid_csv_path, dtype=str)['vid'].to_list()
    vids = ['/innovation_cfs/entertainment/VideoMashup/video/前任3_再见前任.mp4']
    
    for vid_file_path in vids:

        vid = vid_file_path.split('/')[-1].split('.')[0]
        frame_path = os.path.join(args.frames_dir, vid)   
        if not os.path.exists(frame_path):
            os.makedirs(frame_path) 
        os.system('ffmpeg -i '+ vid_file_path + ' -vf fps=1 ' + frame_path +'/%d.jpg')
        frame_name_list = glob.glob(os.path.join(frame_path, '*.jpg')) 
        frame_name_list = sorted(frame_name_list, key=lambda x:int(x.split('/')[-1].split(".")[0])) 
        # if len(frame_name_list) <=5:
        #     pass
        # else:
        #     frame_name_list = random.sample(frame_name_list, 5)
        
        for frame_path in frame_name_list:
            img_save_path = os.path.join(args.feat_save_dir, frame_path.split('/')[-2]+'_'+frame_path.split('/')[-1].split('.')[0]+'.npy')
            bboxes = bbx_extr.extract_bboxes(frame_path)
            bboxes = bboxes.tolist()
            fea = vf_extractor.extract(frame_path, bboxes)
            fea = fea.squeeze(axis=0)
            np.save(img_save_path, fea)
            
