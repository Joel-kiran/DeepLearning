#!/usr/bin/env python
# coding: utf-8

# In[1]:

import numpy as np
import pickle
import pandas as pd
import json
import math
import torch
import string
from random import randint
from collections import defaultdict, Counter
from torch.utils.data import Dataset, DataLoader
from torchvision import transforms, utils
import torch.nn as nn
from torch.autograd import Variable
from torch import optim
import torch.nn.functional as F
import pickle
import random
import pdb


# In[2]:


# file path 
train_id_file = '../MLDS_hw2_1_data/training_data/id.txt'
train_label_file = '../MLDS_hw2_1_data/training_label.json'
test_id_file = '../MLDS_hw2_1_data/testing_data/id.txt'
test_label_file = '../MLDS_hw2_1_data/testing_label.json'
MAXLEN = 80

class Lang:
    def __init__(self, labels, MIN_COUNT=3):
        self.word2index = {"<BOS>": 0, "<EOS>": 1, "<PAD>": 2, "<UNK>": 3}
        self.index2word = {0: "<BOS>", 1: "<EOS>", 2: "<PAD>", 3: "<UNK>"}
        self.vocab = Counter()
        self.vocab_size = 0
        self.min_count = MIN_COUNT
        # construct vocabularly, then construct word2id/id2word
        self.construct_vocab(labels)
        self.construct_word2id()
        
    def addSentence(self, sentence):
        words = sentence.split(' ')
        self.vocab += Counter(words)
    
    def construct_vocab(self, labels):
        for index, row in labels.iterrows():
            for cap in np.unique(np.array(row['caption'])):
                # remove punct and add to vocab
                cap = ' '.join(word.strip(string.punctuation).lower() for word in cap.split())
                self.addSentence(cap)
        # remove word with min_cout < self.min_count
        self.vocab = Counter({vocab:count for vocab, count in self.vocab.items() if count > self.min_count})
        self.vocab_size = len(self.vocab) + 4
        
    def construct_word2id(self):
        # word index start at 4
        word_index = 4
        # construct word2index, index2word
        for word in self.vocab:
            self.word2index[word] = word_index
            self.index2word[word_index] = word
            word_index += 1


class VideoCaptionDataset(Dataset):
    def __init__ (self, train_label_file):
        self.video_label = pd.read_json(train_label_file).set_index('id')
        self.captions = []
        self.caplang = Lang(self.video_label)
        self.video_frames = {}
        # preprocess
        self.preprocess()
        
    def preprocess(self):
        # Preprocess the training data, tokenize video caption and construct (frame, caption) pairs
        counter = 0
        for index, row in self.video_label[:1400].iterrows():
            self.video_frames[index] = torch.FloatTensor(np.load('../MLDS_hw2_1_data/training_data/feat/' + index + '.npy'))
            
            # tokenize caption
            for cap in np.unique(np.array(row['caption'])):
                new_cap = []
                for word in cap.split():
                    word = word.strip(string.punctuation).lower()
                    if word in self.caplang.vocab:
                        new_cap.append(word)
                    else:
                        new_cap.append("<UNK>")
                if (len(new_cap) + 1) > MAXLEN:
                    continue
                # count how many pad should be appended
                cap_num = MAXLEN - (len(new_cap) + 1)
                new_cap += ["<EOS>"]
                new_cap += ["<PAD>"] * cap_num
                new_cap = ' '.join(new_cap)
                self.captions.append([new_cap, index])

    def __len__(self):
        return len(self.captions)
    
    def __getitem__(self, idx):
        caption, vid = self.captions[idx] 
        frame = self.video_frames[vid]
        cap_spt = caption.split(" ")
        cap = [self.caplang.word2index[word] for word in cap_spt]
        cap = torch.LongTensor(cap).view(MAXLEN, 1)
        cap_onehot = torch.LongTensor(MAXLEN, self.caplang.vocab_size)
        cap_onehot.zero_()
        cap_onehot.scatter_(1, cap, 1)
        sample = {'frame': frame, 'onehot': cap_onehot, 'caption': caption}
        return sample

dset = VideoCaptionDataset(train_label_file)
data_size = len(dset)
VOCAB_SIZE = dset.caplang.vocab_size
print("data size: %d, vocab size: %d" % (data_size, VOCAB_SIZE))

valid_frames = []
valid_target = []
valid_label = pd.read_json(train_label_file).set_index('id')

for index, row in valid_label[1400:].iterrows():
    # tokenize caption
    for cap in np.unique(np.array(row['caption'])):
        new_cap = []
        for word in cap.split():
            word = word.strip(string.punctuation).lower()
            if word in dset.caplang.vocab:
                new_cap.append(word)
            else:
                new_cap.append("<UNK>")
        if (len(new_cap) + 1) > MAXLEN:
            continue
        # count how many pad should be appended
        cap_num = MAXLEN - (len(new_cap) + 1)
        new_cap += ["<EOS>"]
        new_cap += ["<PAD>"] * cap_num
        cap = [dset.caplang.word2index[word] for word in new_cap]
        valid_frames.append(np.load('../MLDS_hw2_1_data/training_data/feat/' + index + '.npy'))
        valid_target.append(cap)
valid_frames = Variable(torch.FloatTensor(valid_frames).transpose(0,1))
valid_target = Variable(torch.LongTensor(valid_target).view(-1, MAXLEN))
valid_frames.size(), valid_target.size()

# In[3]:

class Attn(nn.Module):
    def __init__(self, batch_size, hidden_size, dropout=0.3):
        super(Attn, self).__init__()
        self.nn_attn = nn.Linear(hidden_size*2, 1)
        self.dropout = nn.Dropout(p=dropout)
        self.hidden_size = hidden_size
        
    def forward(self, mode, z, encoder_outputs):
        if mode == "nn":
            # (128, 80, 256)
            dup_z = z.transpose(0,1).expand(z.size()[1], 80, self.hidden_size)
            attn_output = self.nn_attn(torch.cat((encoder_outputs.transpose(0,1), dup_z), 2))
        elif mode == "dot":
            # (128, 80, 256) * (128, 256, 1) = (128, 80, 1)
            attn_output = torch.bmm(encoder_outputs.transpose(0,1), z.transpose(0,1).transpose(1,2))
        attn_output = torch.tanh(attn_output)
        attn_weights = F.softmax(attn_output, dim=1)
        return attn_weights
    
class S2VT(nn.Module):
    def __init__(self, feature_size,vocab_size,hidden_size,video_step,output_step,batch_size,n_layers=1,dropout=0.3):
        super(S2VT, self).__init__()
        self.hidden_size = hidden_size
        self.batch_size = batch_size
        self.feature_size = feature_size
        self.embedding_size = 512
        self.video_step = video_step
        self.output_step = output_step
        
        self.attn = Attn(batch_size, hidden_size)
        self.gru1 = nn.GRU(512, hidden_size, n_layers, dropout=dropout)
        self.gru2 = nn.GRU(hidden_size*2+self.embedding_size, hidden_size, n_layers, dropout=dropout)
        self.embedding = nn.Embedding(vocab_size, self.embedding_size)
        self.dropout = nn.Dropout(p=dropout)
        self.fc1 = nn.Linear(feature_size, 512)
        self.out = nn.Linear(hidden_size, vocab_size)
        self.softmax = nn.LogSoftmax(dim=1)
    
    def forward(self, video_seq, cap_seq, teacher_forcing_ratio):
        loss = 0
        # pad MAXLEN, batch, 4096
        padding_gru1 = Variable(torch.zeros(self.output_step, self.batch_size, 512))
        # pad 80, batch, 256
        padding_gru2 = Variable(torch.zeros(self.video_step, self.batch_size, self.hidden_size+self.embedding_size))
        init_BOS = [0] * self.batch_size
        init_BOS = Variable(torch.LongTensor([init_BOS])).resize(batch_size, 1)
        init_BOS = self.embedding(init_BOS)
        
        video_seq = self.dropout(F.selu(self.fc1(video_seq)))
        
        gru1_input = torch.cat((video_seq, padding_gru1), 0)
        # output1:  (seq_len, batch, hidden_size)
        output1, hidden1 = self.gru1(gru1_input)
        
        # cap_seq: batch, MAXLEN => batch, MAXLEN, hidden_size
        embedded = self.embedding(cap_seq)
        gru2_input = torch.cat((padding_gru2, output1[:self.video_step,:,:]),2)
        output2, decoder_hidden = self.gru2(gru2_input)
        z = decoder_hidden
        # decoder
        for step in range(self.output_step):
            use_teacher_forcing = True if random.random() <= teacher_forcing_ratio else False
            if step == 0:
                decoder_input = init_BOS
            elif use_teacher_forcing:
                decoder_input = embedded[:,step-1,:].unsqueeze(1)
            else:
                decoder_input = decoder_output.max(1)[-1].resize(batch_size, 1)
                decoder_input = self.embedding(decoder_input)
            
            attn_weights = self.attn('dot', z, output1[:self.video_step])
            c = torch.bmm(attn_weights.transpose(1,2),
                                 output1[:self.video_step].transpose(0,1))
            # batch, 1, hidden_size*2
            gru2_input = torch.cat((decoder_input, output1[self.video_step+step].unsqueeze(1), c),2).transpose(0,1)
            
            decoder_output, z = self.gru2(gru2_input, z)
            decoder_output = self.softmax(self.out(decoder_output[0]))
            loss += F.nll_loss(decoder_output, cap_seq[:,step])
        return loss
    
    def attn_reg(self, attns):
        time_sum = attns.sum(2)
        tao = time_sum.mean(1).resize(self.batch_size, 1).expand(self.batch_size, time_sum.size()[1])
        reg = torch.pow((tao - time_sum), 2).sum()
        return reg
    
    def testing(self, video_seq, dset, use_beam_search, beam_size):
        pred = []
        padding_gru1 = Variable(torch.zeros(self.output_step, 1, 512))
        padding_gru2 = Variable(torch.zeros(self.video_step, 1, self.hidden_size+self.embedding_size))
        init_BOS = [0]
        init_BOS = Variable(torch.LongTensor([init_BOS])).resize(1, 1)
        init_BOS = self.embedding(init_BOS)
        
        video_seq = F.selu(self.fc1(video_seq))
        
        gru1_input = torch.cat((video_seq, padding_gru1), 0)
        output1, hidden1 = self.gru1(gru1_input)
        
        gru2_input = torch.cat((padding_gru2, output1[:self.video_step,:,:]),2)
        output2, decoder_hidden = self.gru2(gru2_input)
        z = decoder_hidden
        
        for step in range(self.output_step):
            if step == 0:
                decoder_input = init_BOS
            else:
                decoder_input = decoder_output.max(1)[-1].resize(1, 1)
                decoder_input = self.embedding(decoder_input)
            attn_weights = self.attn('dot', z, output1[:self.video_step])
            c = torch.bmm(attn_weights.transpose(1,2),
                                 output1[:self.video_step].transpose(0,1))
            # batch, 1, hidden_size*2
            gru2_input = torch.cat((decoder_input, output1[self.video_step+step].unsqueeze(1), c),2).transpose(0,1)

            decoder_output, z = self.gru2(gru2_input, z)
            decoder_output = self.softmax(self.out(decoder_output[0]))
            output = decoder_output.max(1)[-1].resize(1, 1)
            word2ix = output.data[0,0]
            ix2word = dset.caplang.index2word[word2ix.item()]
            if word2ix < 3:
                break
            else:
                pred.append(ix2word)
        return pred
    
    def validation(self, video_seq, cap_seq, valid_size):
        loss = 0
        padding_gru1 = Variable(torch.zeros(self.output_step, valid_size, 512))
        padding_gru2 = Variable(torch.zeros(self.video_step, valid_size, self.hidden_size+self.embedding_size))
        init_BOS = [0] * valid_size
        init_BOS = Variable(torch.LongTensor([init_BOS])).resize(valid_size, 1)
        init_BOS = self.embedding(init_BOS)
        
        video_seq = F.selu(self.fc1(video_seq))
        
        gru1_input = torch.cat((video_seq, padding_gru1), 0)
        output1, hidden1 = self.gru1(gru1_input)
        
        gru2_input = torch.cat((padding_gru2, output1[:self.video_step,:,:]),2)
        output2, decoder_hidden = self.gru2(gru2_input)
        z = decoder_hidden
        
        for step in range(self.output_step):
            if step == 0:
                decoder_input = init_BOS
            else:
                decoder_input = decoder_output.max(1)[-1].resize(valid_size, 1)
                decoder_input = self.embedding(decoder_input)
            attn_weights = self.attn('dot', z, output1[:self.video_step])
            c = torch.bmm(attn_weights.transpose(1,2),
                                 output1[:self.video_step].transpose(0,1))
            # batch, 1, hidden_size*2
            gru2_input = torch.cat((decoder_input, output1[self.video_step+step].unsqueeze(1), c),2).transpose(0,1)
            
            decoder_output, z = self.gru2(gru2_input, z)
            decoder_output = self.softmax(self.out(decoder_output[0]))
            loss += F.nll_loss(decoder_output, cap_seq[:,step])
        return loss

# In[4]:

def count_parameters(model):
    return sum(p.numel() for p in model.parameters() if p.requires_grad)


# In[4]:

hidden_size = 512
batch_size = 64
feature_size = 4096
seq_len = 80
iter_size = data_size // batch_size
dataloader = DataLoader(dset, batch_size=batch_size, shuffle=True)
s2vt_model = S2VT(feature_size,VOCAB_SIZE,hidden_size,seq_len,MAXLEN,batch_size)
s2vt_opti = optim.Adam(s2vt_model.parameters(), lr = 0.001)

print("S2VT model parameters count: %d" % (count_parameters(s2vt_model)))

# In[7]:

epoches = 100
min_train_loss = 99
training_loss=[]

# In[9]:

for epoch in range(epoches+1):
    s2vt_model.train()
    vid = 0
    teacher_forcing_ratio = 0.06
    epoch_losses = 0
    for i, batch_data in enumerate(dataloader):
        if i == iter_size:
            break
        s2vt_opti.zero_grad()
        target = Variable(batch_data['onehot'])
        video_seq = Variable(batch_data['frame'].transpose(0, 1))
        loss = s2vt_model(video_seq, target.max(2)[-1], teacher_forcing_ratio)
        epoch_losses += loss.item() / MAXLEN
        loss.backward()
        s2vt_opti.step()

    train_loss= epoch_losses/iter_size
    if train_loss < min_train_loss:
        min_train_loss = train_loss
        torch.save(s2vt_model.state_dict(), "seq2seq_model")
    training_loss.append(train_loss)
    print("[Epoch %d] Loss: %f" % (epoch+1, epoch_losses/iter_size))

    
# In[10]:

test_frames = {}
test_label = pd.read_json(test_label_file).set_index('id')
for index, row in test_label.iterrows():
    test_frames[index] = torch.FloatTensor(np.load('../MLDS_hw2_1_data/testing_data/feat/' + index + '.npy'))

s2vt_model.eval()
predictions = []
indices = []
use_beam_search = False
beam_size = 2
for i, row in test_label.iterrows():
    video_input = Variable(test_frames[i].view(-1, 1, feature_size))
    pred = s2vt_model.testing(video_input, dset, use_beam_search, beam_size)
    pred[0] = pred[0].title()
    pred = " ".join(pred)
    predictions.append(pred)
    indices.append(i)
    print(i + " / " + pred)


with open('result.txt', 'w') as result_file:
    for i in range(100):
        result_file.write(indices[i] + "," + predictions[i] + "\n")


# In[11]:

import matplotlib.pyplot as plt
plt1=plt.figure(figsize=(10, 6))
plt.plot(np.array(training_loss), 'r', label='training loss')
#plt.plot(np.array(validation_loss), 'b', label='validation loss')

# naming the x axis
plt.xlabel('Epochs')
# naming the y axis
plt.ylabel('Loss') 
plt.legend()
plt.title("Loss function")
plt.show()




