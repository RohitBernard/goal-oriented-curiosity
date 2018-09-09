import ray
import gym
import os
import random
from itertools import chain

import numpy as np

import torch.nn.functional as F
import torch.nn as nn
import torch
import cv2

from model import *

import torch.optim as optim
from torch.multiprocessing import Pipe, Process

from collections import deque

from tensorboardX import SummaryWriter


class PongEnvironment(Process):
    def __init__(self, env_id, is_render, env_idx, child_conn, writer, history_size=4, h=84, w=84):
        super(PongEnvironment, self).__init__()
        self.daemon = True
        self.env = gym.make(env_id)

        self.is_render = is_render
        self.env_idx = env_idx
        self.steps = 0
        self.episode = 0
        self.rall = 0
        self.recent_rlist = deque(maxlen=100)
        self.child_conn = child_conn

        self.history_size = history_size
        self.history = np.zeros([history_size, h, w])
        self.h = h
        self.w = w
        self.writer = writer

        self.reset()

    def run(self):
        super(PongEnvironment, self).run()
        while True:
            action = self.child_conn.recv()
            if self.is_render:
                self.env.render()
            _, reward, done, info = self.env.step(action)

            self.history[:3, :, :] = self.history[1:, :, :]
            self.history[3, :, :] = self.pre_proc(self.env.env.ale.getScreenGrayscale().squeeze().astype('float32'))

            self.rall += reward
            self.steps += 1

            if done:
                self.recent_rlist.append(self.rall)
                print("[Episode {}({})] Step: {}  Reward: {}  Recent Reward: {}".format(self.episode, self.env_idx,
                                                                                        self.steps, self.rall,
                                                                                        np.mean(self.recent_rlist)))
                self.writer.add_scalar('data/env{}/reward'.format(self.env_idx), self.rall, self.episode)
                self.writer.add_scalar('data/env{}/step'.format(self.env_idx), self.steps, self.episode)

                self.history = self.reset()

            self.child_conn.send([self.history[:, :, :], np.clip(reward, -1, 1), done, info])

    def reset(self):
        self.steps = 0
        self.episode += 1
        self.rall = 0
        self.env.reset()
        self.get_init_state(self.env.env.ale.getScreenGrayscale().squeeze().astype('float32'))
        return self.history[:, :, :]

    def pre_proc(self, X):
        x = cv2.resize(X, (self.h, self.w))
        x *= (1.0 / 255.0)

        return x

    def get_init_state(self, s):
        for i in range(self.history_size):
            self.history[i, :, :] = self.pre_proc(s)


class ActorAgent(object):
    def __init__(self, input_size, output_size, num_env, num_step, gamma, lam=0.95, use_gae=True, use_cuda=False):
        self.model = CnnActorCriticNetwork(input_size, output_size)
        self.num_env = num_env
        self.output_size = output_size
        self.input_size = input_size
        self.num_step = num_step
        self.gamma = gamma
        self.lam = lam
        self.use_gae = use_gae
        self.optimizer = optim.RMSprop(self.model.parameters(), lr=learning_rate, eps=epslion, alpha=alpha)
        self.device = torch.device('cuda' if use_cuda else 'cpu')

        self.model = self.model.to(self.device)

    def get_action(self, state):
        state = torch.Tensor(state).to(self.device)
        state = state.float()
        policy, value = self.model(state)
        policy = F.softmax(policy, dim=-1).data.cpu().numpy()

        action = self.random_choice_prob_index(policy)

        return action

    @staticmethod
    def random_choice_prob_index(p, axis=1):
        r = np.expand_dims(np.random.rand(p.shape[1 - axis]), axis=axis)
        return (p.cumsum(axis=axis) > r).argmax(axis=axis)

    def forward_transition(self, state, next_state):
        state = torch.from_numpy(state).to(self.device)
        state = state.float()
        _, value = agent.model(state)

        next_state = torch.from_numpy(next_state).to(self.device)
        next_state = next_state.float()
        _, next_value = agent.model(next_state)

        value = value.data.cpu().numpy().squeeze()
        next_value = next_value.data.cpu().numpy().squeeze()

        return value, next_value

    def train_model(self, s_batch, target_batch, y_batch, adv_batch):
        with torch.no_grad():
            s_batch = torch.FloatTensor(s_batch).to(self.device)
            target_batch = torch.FloatTensor(target_batch).to(self.device)
            y_batch = torch.LongTensor(y_batch).to(self.device)
            adv_batch = torch.FloatTensor(adv_batch).to(self.device)

        # for multiply advantage
        policy, value = self.model(s_batch)
        m = Categorical(F.softmax(policy, dim=-1))

        # mse = nn.SmoothL1Loss()
        mse = nn.MSELoss()

        # Actor loss
        actor_loss = -m.log_prob(y_batch) * adv_batch

        # Entropy(for more exploration)
        entropy = m.entropy()

        # Critic loss
        critic_loss = mse(value.sum(1), target_batch)

        # Total loss
        loss = actor_loss.mean() + 0.5 * critic_loss - 0.01 * entropy.mean()

        self.optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(self.model.parameters(), 3)
        self.optimizer.step()


def make_train_data(reward, done, value, next_value):
    discounted_return = np.empty([num_step])

    # Discounted Return
    if use_gae:
        gae = 0
        for t in range(num_step - 1, -1, -1):
            delta = reward[t] + gamma * next_value[t] * (1 - done[t]) - value[t]
            gae = delta + gamma * lam * (1 - done[t]) * gae

            discounted_return[t] = gae + value[t]

        # For critic
        target = reward + gamma * (1 - done) * next_value

        # For Actor
        adv = discounted_return - value

    else:
        running_add = next_value[num_step - 1, 0] * (1 - done[num_step - 1, 0])
        for t in range(num_step - 1, -1, -1):
            if d[t]:
                running_add = 0
            running_add = reward[t] + gamma * running_add
            discounted_return[t] = running_add

        # For critic
        target = r + gamma * (1 - done) * next_value

        # For Actor
        adv = discounted_return - value

    return target, adv


if __name__ == '__main__':
    env_id = 'PongDeterministic-v4'
    env = gym.make(env_id)
    input_size = env.observation_space.shape  # 4
    output_size = env.action_space.n  # 2

    env.close()

    writer = SummaryWriter()
    use_cuda = True
    use_gae = True

    lam = 0.95
    num_worker = 16
    num_worker_per_env = 1
    num_step = 5
    learning_rate = 0.0007 * num_worker
    epslion = 0.1
    entropy = 0.02
    alpha = 0.99
    gamma = 0.99
    agent = ActorAgent(input_size, output_size, num_worker_per_env * num_worker, num_step, gamma, use_cuda=use_cuda)
    is_render = False

    works = []
    parent_conns = []
    child_conns = []
    for idx in range(num_worker):
        parent_conn, child_conn = Pipe()
        work = PongEnvironment(env_id, is_render, idx, child_conn, writer)
        work.start()
        works.append(work)
        parent_conns.append(parent_conn)
        child_conns.append(child_conn)

    states = np.zeros([num_worker * num_worker_per_env, 4, 84, 84])

    while True:
        total_state, total_reward, total_done, total_next_state, total_action = [], [], [], [], []

        for _ in range(num_step):
            actions = agent.get_action(states)

            for parent_conn, action in zip(parent_conns, actions):
                parent_conn.send(action)

            total_next_state.append(states)
            states, rewards, dones, next_states = [], [], [], []
            for parent_conn in parent_conns:
                s, r, d, _ = parent_conn.recv()
                states.append(s)
                rewards.append(r)
                dones.append(d)

            states = np.stack(states)
            rewards = np.hstack(rewards)
            dones = np.hstack(dones)

            total_state.append(states)
            total_reward.append(rewards)
            total_done.append(dones)
            total_action.append(actions)

        total_state = np.stack(total_state).transpose([1, 0, 2, 3, 4]).reshape([-1, 4, 84, 84])
        total_next_state = np.stack(total_next_state).transpose([1, 0, 2, 3, 4]).reshape([-1, 4, 84, 84])
        total_reward = np.stack(total_reward).transpose().reshape([-1])
        total_action = np.stack(total_action).transpose().reshape([-1])
        total_done = np.stack(total_done).transpose().reshape([-1])

        value, next_value = agent.forward_transition(total_state, total_next_state)

        total_target = []
        total_adv = []
        for idx in range(num_worker):
            target, adv = make_train_data(total_reward[idx * num_step:(idx + 1) * num_step],
                                          total_done[idx * num_step:(idx + 1) * num_step],
                                          value[idx * num_step:(idx + 1) * num_step],
                                          next_value[idx * num_step:(idx + 1) * num_step])
            # print(target.shape)
            total_target.append(target)
            total_adv.append(adv)

        agent.train_model(total_state, np.hstack(total_target), total_action, np.hstack(total_adv))
