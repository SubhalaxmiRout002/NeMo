# =============================================================================
# Copyright 2020 NVIDIA. All Rights Reserved.
# Copyright 2018 The Google AI Language Team Authors and
# The HuggingFace Inc. team.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# =============================================================================

"""
Utility functions for Token Classification NLP tasks
Some parts of this code were adapted from the HuggingFace library at
https://github.com/huggingface/pytorch-pretrained-BERT
"""

import numpy as np
from torch.utils.data import Dataset
from collections import defaultdict
import random

from nemo import logging
from nemo.collections.nlp.data.datasets.datasets_utils import get_stats

__all__ = ['BertJointIntentSlotDataset', 'BertJointIntentSlotInferDataset', 'LabeledAugmentation', 'value_replacement']

def value_replacement(
    words,
    raw_slot,
    slot_to_value_mapping,
    prob_to_change,
):
    for j, word in enumerate(words):
        alternative_values = slot_to_value_mapping.get(raw_slot[j], None)
        if alternative_values:
            if random.random() < prob_to_change:
                words[j] = random.choice(alternative_values)

    return words

def get_features(
    query,
    max_seq_length,
    tokenizer,
    pad_label=128,
    raw_slot=None,
    ignore_extra_tokens=False,
    ignore_start_end=False,
    with_label=False,
):

    words = query.strip().split()
    subtokens = [tokenizer.cls_token]
    loss_mask = [1 - ignore_start_end]
    subtokens_mask = [0]
    if with_label:
        slots = [pad_label]

    for j, word in enumerate(words):
        word_tokens = tokenizer.text_to_tokens(word)
        subtokens.extend(word_tokens)

        loss_mask.append(1)
        loss_mask.extend([not ignore_extra_tokens] * (len(word_tokens) - 1))

        subtokens_mask.append(1)
        subtokens_mask.extend([0] * (len(word_tokens) - 1))

        if with_label:
            slots.extend([raw_slot[j]] * len(word_tokens))

    subtokens.append(tokenizer.sep_token)
    loss_mask.append(1 - ignore_start_end)
    subtokens_mask.append(0)
    input_mask = [1] * len(subtokens)
    if with_label:
        slots.append(pad_label)


    if len(subtokens) > max_seq_length:
        subtokens = [tokenizer.cls_token] + subtokens[-max_seq_length + 1 :]
        input_mask = [1] + input_mask[-max_seq_length + 1 :]
        loss_mask = [1 - ignore_start_end] + loss_mask[-max_seq_length + 1 :]
        subtokens_mask = [0] + subtokens_mask[-max_seq_length + 1 :]

        if with_label:
            slots = [pad_label] + slots[-max_seq_length + 1 :]

    input_ids = [tokenizer.tokens_to_ids(t) for t in subtokens]

    if len(subtokens) < max_seq_length:
        extra = max_seq_length - len(subtokens)
        input_ids = input_ids + [0] * extra
        loss_mask = loss_mask + [0] * extra
        subtokens_mask = subtokens_mask + [0] * extra
        input_mask = input_mask + [0] * extra

        if with_label:
            slots = slots + [pad_label] * extra

    segment_ids = [0] * max_seq_length
    return (input_ids, segment_ids, input_mask, loss_mask, subtokens_mask, slots)





class BertJointIntentSlotDataset(Dataset):
    """
    Creates dataset to use for the task of joint intent
    and slot classification with pretrained model.

    Converts from raw data to an instance that can be used by
    NMDataLayer.

    For dataset to use during inference without labels, see
    BertJointIntentSlotInferDataset.

    Args:
        input_file (str): file to sequence + label.
            the first line is header (sentence [tab] label)
            each line should be [sentence][tab][label]
        slot_file (str): file to slot labels, each line corresponding to
            slot labels for a sentence in input_file. No header.
        max_seq_length (int): max sequence length minus 2 for [CLS] and [SEP]
        tokenizer (Tokenizer): such as NemoBertTokenizer
        num_samples (int): number of samples you want to use for the dataset.
            If -1, use all dataset. Useful for testing.
        pad_label (int): pad value use for slot labels.
            by default, it's the neutral label.

    """

    def __init__(
        self,
        input_file,
        slot_file,
        max_seq_length,
        tokenizer,
        num_samples=-1,
        pad_label=128,
        ignore_extra_tokens=False,
        ignore_start_end=False,
        do_lower_case=False,
    ):
        if num_samples == 0:
            raise ValueError("num_samples has to be positive", num_samples)

        self.ignore_extra_tokens = ignore_extra_tokens
        self.ignore_start_end = ignore_start_end
        self.do_lower_case = do_lower_case
        self.pad_label = pad_label
        self.tokenizer = tokenizer


        with open(slot_file, 'r') as f:
            slot_lines = f.readlines()

        with open(input_file, 'r') as f:
            input_lines = f.readlines()[1:]

        assert len(slot_lines) == len(input_lines)

        dataset = list(zip(slot_lines, input_lines))

        if num_samples > 0:
            dataset = dataset[:num_samples]

        self.raw_slots, queries, raw_intents, self.all_words = [], [], [], []
        for slot_line, input_line in dataset:
            self.raw_slots.append([int(slot) for slot in slot_line.strip().split()])
            parts = input_line.strip().split()
            raw_intents.append(int(parts[-1]))
            query = ' '.join(parts[:-1])
            if do_lower_case:
                query = query.lower()
            self.all_words.append(query.strip().split())
            queries.append(query)

        self.queries = queries
        self.max_seq_length = max_seq_length

        features = list(zip(*[get_features(
            queries[idx],
            max_seq_length,
            tokenizer,
            pad_label=pad_label,
            raw_slot=self.raw_slots[idx],
            ignore_extra_tokens=ignore_extra_tokens,
            ignore_start_end=ignore_start_end,
            with_label=True,
        ) for idx in range(len(queries))]))

        self.all_input_ids = features[0]
        self.all_segment_ids = features[1]
        self.all_input_mask = features[2]
        self.all_loss_mask = features[3]
        self.all_subtokens_mask = features[4]
        self.all_slots = features[5]
        self.all_intents = raw_intents
        # self.max_seq_length = features[6]


    def __len__(self):
        return len(self.all_input_ids)

    def __getitem__(self, idx):
        return (
            np.array(self.all_input_ids[idx]),
            np.array(self.all_segment_ids[idx]),
            np.array(self.all_input_mask[idx], dtype=np.long),
            np.array(self.all_loss_mask[idx]),
            np.array(self.all_subtokens_mask[idx]),
            self.all_intents[idx],
            np.array(self.all_slots[idx]),
        )

class BertJointIntentSlotInferDataset(Dataset):
    """
    Creates dataset to use for the task of joint intent
    and slot classification with pretrained model.

    Converts from raw data to an instance that can be used by
    NMDataLayer.

    This is to be used during inference only.
    For dataset to use during training with labels, see
    BertJointIntentSlotDataset.

    Args:
        queries (list): list of queries to run inference on
        max_seq_length (int): max sequence length minus 2 for [CLS] and [SEP]
        tokenizer (Tokenizer): such as NemoBertTokenizer
        pad_label (int): pad value use for slot labels.
            by default, it's the neutral label.

    """

    def __init__(self, queries, max_seq_length, tokenizer, do_lower_case):
        if do_lower_case:
            for idx, query in enumerate(queries):
                queries[idx] = queries[idx].lower()

        features = get_features(queries, max_seq_length, tokenizer)

        self.all_input_ids = features[0]
        self.all_segment_ids = features[1]
        self.all_input_mask = features[2]
        self.all_loss_mask = features[3]
        self.all_subtokens_mask = features[4]

    def __len__(self):
        return len(self.all_input_ids)

    def __getitem__(self, idx):
        return (
            np.array(self.all_input_ids[idx]),
            np.array(self.all_segment_ids[idx]),
            np.array(self.all_input_mask[idx], dtype=np.long),
            np.array(self.all_loss_mask[idx]),
            np.array(self.all_subtokens_mask[idx]),
        )


class LabeledAugmentation(Dataset):

    def __init__(self, dataset, augmentation_func):
        self._dataset = dataset
        self.slot_value_mapping = self.get_slot_value_mapping(all_words=self._dataset.all_words, pad_label=self._dataset.pad_label, raw_slots=self._dataset.raw_slots)
        self.augmentation_func = augmentation_func

    def __len__(self):
        return len(self._dataset)

    def __getitem__(self, idx):
        words = self.augmentation_func(
            self._dataset.queries[idx].strip().split(),
            self._dataset.raw_slots[idx],
            self.slot_value_mapping)
        query = " ".join(words)
        features =  get_features(
                    query=query,
                    max_seq_length=self._dataset.max_seq_length,
                    tokenizer=self._dataset.tokenizer,
                    pad_label=self._dataset.pad_label,
                    raw_slot=self._dataset.raw_slots[idx],
                    ignore_extra_tokens=self._dataset.ignore_extra_tokens,
                    ignore_start_end=self._dataset.ignore_start_end,
                    with_label=self._dataset.raw_slots is not None)
        return (
            np.array(features[0]),
            np.array(features[1]),
            np.array(features[2], dtype=np.long),
            np.array(features[3]),
            np.array(features[4]),
            self._dataset.all_intents[idx],
            np.array(features[5]),
        )


    def asd(self):
        words = self._dataset.all_words[idx]
        # raw_slot = self._dataset.all_slots[idx]
        raw_slot = self._dataset[idx][6]
        max_seq_length = self._dataset.max_seq_length
        ignore_start_end = self._dataset.ignore_start_end
        ignore_extra_tokens = self._dataset.ignore_extra_tokens
        words = self.augmentation_func(words, raw_slot, self.slot_value_mapping)

        subtokens = [self._dataset.tokenizer.cls_token]
        loss_mask = [1 - self._dataset.ignore_start_end]
        subtokens_mask = [0]
        slots = [self._dataset.pad_label]

        for j, word in enumerate(words):
            word_tokens = self._dataset.tokenizer.text_to_tokens(word)
            subtokens.extend(word_tokens)

            loss_mask.append(1)
            loss_mask.extend([not self._dataset.ignore_extra_tokens] * (len(word_tokens) - 1))

            subtokens_mask.append(1)
            subtokens_mask.extend([0] * (len(word_tokens) - 1))

            slots.extend([raw_slot[j]] * len(word_tokens))

        subtokens.append(self._dataset.tokenizer.sep_token)
        loss_mask.append(1 - self._dataset.ignore_start_end)
        subtokens_mask.append(0)
        input_mask = [1] * len(subtokens)
        slots.append(self._dataset.pad_label)

        if len(subtokens) > max_seq_length:
            subtokens = [self._dataset.tokenizer.cls_token] + subtokens[-max_seq_length + 1 :]
            input_mask = [1] + input_mask[-max_seq_length + 1 :]
            loss_mask = [1 - ignore_start_end] + loss_mask[-max_seq_length + 1 :]
            subtokens_mask = [0] + subtokens_mask[-max_seq_length + 1 :]
            slots = [self._dataset.pad_label] + slots[-max_seq_length + 1 :]

        input_ids = [self._dataset.tokenizer.tokens_to_ids(t) for t in subtokens]

        if len(subtokens) < max_seq_length:
            extra = max_seq_length - len(subtokens)
            input_ids = input_ids + [0] * extra
            loss_mask = loss_mask + [0] * extra
            subtokens_mask = subtokens_mask + [0] * extra
            input_mask = input_mask + [0] * extra

            slots = slots + [self._dataset.pad_label] * extra

        segment_ids = [0] * max_seq_length
        return (np.array(input_ids), np.array(segment_ids), np.array(input_mask, dtype=np.long), np.array(loss_mask), np.array(subtokens_mask), self._dataset.all_intents[idx], np.array(slots))

    def get_slot_value_mapping(
        self,
        all_words,
        pad_label,
        raw_slots=None):
        slot_values = defaultdict(set)
        for i, words in enumerate(all_words):
            for j, word in enumerate(words):
                if raw_slots[i][j] != pad_label:
                    slot_values[raw_slots[i][j]].add(word)
        for k, v in slot_values.items():
            slot_values[k] = list(v)
        return slot_values




class UnlabeledAugmentation(Dataset):

    def __init__(self, dataset):
        self.dataset = dataset

