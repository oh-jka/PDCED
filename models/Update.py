#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Python version: 3.6

from itertools import accumulate
from bs4 import TemplateString
from numpy import imag
import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset
from tqdm import tqdm
import math
import pdb

class DatasetSplit(Dataset):
    def __init__(self, dataset, idxs):
        self.dataset = dataset
        self.idxs = list(idxs)

    def __len__(self):
        return len(self.idxs)

    def __getitem__(self, item):
        image, label = self.dataset[self.idxs[item]]
        return image, label


class LocalUpdate(object):
    def __init__(self, args, dataset=None, idxs=None, pretrain=False):
        self.args = args
        self.loss_func = nn.CrossEntropyLoss()
        # self.selected_clients = []
        self.ldr_train = DataLoader(DatasetSplit(dataset, idxs), batch_size=self.args.local_bs, shuffle=True)
        self.pretrain = pretrain

    def train(self, net, idx=-1, lr=0.1):
        net.train()
        # train and update
        optimizer = torch.optim.SGD(net.parameters(), lr=lr, momentum=self.args.momentum)
        epoch_loss = []
        if self.pretrain:
            local_eps = self.args.local_ep_pretrain
        else:
            local_eps = self.args.local_ep
        
        # 上传梯度时，局部只更新1轮
        # assert local_eps == 1
        # batch_loss = []
        # grads_local = []
        # net.zero_grad()
        # for batch_idex, (images, labels) in enumerate(self.ldr_train):
        #     images, labels = images.to(self.args.device), labels.to(self.args.device)
        #     log_probs = net(images)
        #     loss = self.loss_func(log_probs, labels)
        #     loss.backward()
        #     batch_loss.append(loss.item())
        #     # 获得梯度
        #     for para in net.parameters():
        #         # grads_local.append(torch.div(para.grad.detach(), len(batch_loss)))
        #         grads_local.append(para.grad.detach()/len(batch_loss))
        #     return grads_local, sum(batch_loss)/len(batch_loss)
        
        # 上传累积梯度
        accumulated_grads_local = []
        for iter in range(local_eps):
            batch_loss = []
            for batch_idx, (images, labels) in enumerate(self.ldr_train):
                images, labels = images.to(self.args.device), labels.to(self.args.device)
                net.zero_grad()
                log_probs = net(images)
                loss = self.loss_func(log_probs, labels)
                loss.backward()
                optimizer.step()
                
                # 累积梯度记录
                if len(accumulated_grads_local) == 0:
                    for para in net.parameters():
                        # 注意要从计算图分离出来并并保存到新的内存地址
                        accumulated_grads_local.append(para.grad.detach().clone())
                else:
                    for level, para in enumerate(net.parameters()):
                        accumulated_grads_local[level] += para.grad
                        
                batch_loss.append(loss.item())
            epoch_loss.append(sum(batch_loss)/len(batch_loss))

        # 从GPU转移到CPU:协议处理时使用numpy和其他库处理
        accumulated_grads_local = [grad.cpu() for grad in accumulated_grads_local]
        return accumulated_grads_local, sum(epoch_loss)/len(epoch_loss)


        # 上传参数
        # for iter in range(local_eps):
        #     batch_loss = []
        #     for batch_idx, (images, labels) in enumerate(self.ldr_train):
        #         images, labels = images.to(self.args.device), labels.to(self.args.device)
        #         net.zero_grad()
        #         log_probs = net(images)
        #         loss = self.loss_func(log_probs, labels)
        #         loss.backward()
        #         optimizer.step()
        #         batch_loss.append(loss.item())
        #     epoch_loss.append(sum(batch_loss)/len(batch_loss))
        # return net.state_dict(), sum(epoch_loss) / len(epoch_loss)


class LocalUpdateMTL(object):
    def __init__(self, args, dataset=None, idxs=None, pretrain=False):
        self.args = args
        self.loss_func = nn.CrossEntropyLoss()
        # self.selected_clients = []
        self.ldr_train = DataLoader(DatasetSplit(dataset, idxs), batch_size=self.args.local_bs, shuffle=True)
        self.pretrain = pretrain

    def train(self, net, lr=0.1, omega=None, W_glob=None, idx=None, w_glob_keys=None):
        net.train()
        # train and update
        optimizer = torch.optim.SGD(net.parameters(), lr=lr, momentum=0.1)

        epoch_loss = []
        if self.pretrain:
            local_eps = self.args.local_ep_pretrain
        else:
            local_eps = self.args.local_ep

        for iter in range(local_eps):
            batch_loss = []
            for batch_idx, (images, labels) in enumerate(self.ldr_train):
                images, labels = images.to(self.args.device), labels.to(self.args.device)
                net.zero_grad()
                log_probs = net(images)

                loss = self.loss_func(log_probs, labels)

                W = W_glob.clone()

                W_local = [net.state_dict(keep_vars=True)[key].flatten() for key in w_glob_keys]
                W_local = torch.cat(W_local)
                W[:, idx] = W_local

                loss_regularizer = 0
                loss_regularizer += W.norm() ** 2

                k = 4000
                for i in range(W.shape[0] // k):
                    x = W[i * k:(i+1) * k, :]
                    loss_regularizer += x.mm(omega).mm(x.T).trace()
                f = (int)(math.log10(W.shape[0])+1) + 1
                loss_regularizer *= 10 ** (-f)

                loss = loss + loss_regularizer
                loss.backward()
                optimizer.step()

                batch_loss.append(loss.item())

            epoch_loss.append(sum(batch_loss)/len(batch_loss))

        return net.state_dict(), sum(epoch_loss) / len(epoch_loss)
