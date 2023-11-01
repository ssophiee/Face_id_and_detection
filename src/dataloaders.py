import os
from pathlib import Path
import random

import pandas as pd
import numpy as np

import torch
from torchvision import models, transforms, datasets
from torch.utils.data import DataLoader, Dataset, ConcatDataset

import cv2
from PIL import Image
import albumentations as A

import src.utils as utils


config = utils.get_options()

batch_size = config['batch_size']
img_size = config['img_size']
img_size_recog = config['img_size_recog']

if config['use_colab']:
    root = '/content/Face_id_and_detection/'
else:
    root = ''

# пути к папкам датасета с 3к изображениям лиц
y_labels = pd.read_csv(f'{root}data/human-faces-object-detection/faces.csv')
image_path = f'{root}data/human-faces-object-detection/images'

# путь к папке с картинками комнат
backg_image_path = f'{root}data/house-rooms-image-dataset/House_Room_Dataset'


class FacesDataset(Dataset):

    def __init__(self, images_path, dataset, transform_bbox, transform, height, width):
        ''' Loading dataset
        images_path: path where images are stored
        dataset: dataframe where image names and box bounds are stored
        transform: functions for data augmentation (from albumentations lib)
        height: height used to resize the image
        width: width used to resize the image
        images_list: list where all image paths are stored
        bboxes: list where all the bounding boxes are stored
        '''
        self.images_path = Path(images_path)
        self.dataset = dataset

        self.n_samples = dataset.shape[0]

        self.images_list = sorted(list(self.images_path.glob('*.jpg')))
        self.images_names = [image.name for image in self.images_list]
        self.bboxes_names = dataset['image_name'].tolist()

        self.transform_bbox = transform_bbox
        self.transform = transform

        self.height = height
        self.width = width

   # cut down to only images present in dataset

        self.images = []
        for i in self.bboxes_names:
            for j in self.images_names:
                if i == j:
                    self.images.append(i)

    def __getitem__(self, index):

        image_name = self.images[index]
        image_path = self.images_path / image_name

        img = cv2.imread(str(image_path))
        # by default in cv2 represents image in BGR order, so we have to convert it back to RGB
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        
        img = cv2.resize(img, (250, 250)).astype(np.float32)
        img /= 255.0 # normalizing values
        # img = np.transpose(img, (2, 0, 1))

        image_labels = self.dataset[self.dataset['image_name'] == image_name]

        imgs, bbox = [], []
        for i in range(len(image_labels)):
            cur_height = image_labels['height'].iloc[i]
            cur_width = image_labels['width'].iloc[i]

            x0 = (int(image_labels['x0'].iloc[i]) / cur_width) * self.width
            y0 = (int(image_labels['y0'].iloc[i]) / cur_height) * self.height
            x1 = (int(image_labels['x1'].iloc[i]) / cur_width)  * self.width
            y1 = (int(image_labels['y1'].iloc[i]) / cur_height) * self.height

            bbox = torch.tensor([1, x0, y0, x1, y1]).float()
            break

        if self.transform_bbox:
            items = self.transform_bbox(image=img, bboxes=[list(bbox[1:])], class_labels=[1])
            img = np.transpose(items['image'], (2, 0, 1)) 
            img = items['image'] # converting back to CHW format

            if len(items['bboxes']) > 0:
                bbox = torch.tensor([1] + list(items['bboxes'][0]))
            else:
                # if bbox is too small after the augmentation we drop the bbox
                bbox = torch.tensor([0, -1, -1, -1, -1])

        if self.transform:
            img = self.transform(img)

        return img, bbox

    def __len__(self):
        return self.n_samples


class BackgroundDataset(Dataset):

    def __init__(self, folder_path, transform, height=64, width=64):
        ''' Loading dataset
        folder_path: path of images of background
        '''
        self.folder_path = Path(folder_path)
        self.height = height
        self.width = width
        self.transform = transform

        self.types = ['Bathroom', 'Bedroom',
                      'Dinning', 'Kitchen', 'Livingroom']

        images_path = []
        for type in self.types:
            type_folder = os.path.join(folder_path, type)
            images = os.listdir(type_folder)
            images_path += [os.path.join("data", os.path.relpath(os.path.join(type_folder, img), 'data/'))
                            for img in images]
        random.shuffle(images_path)
        self.images_path = images_path[:2500]

        self.n_samples = len(self.images_path)

    def __getitem__(self, index):
        image_path = self.images_path[index]
        img = cv2.imread(image_path)
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img = cv2.resize(img, (self.width, self.height)).astype(np.float32)
        img /= 255.0
        img = np.transpose(img, (2, 0, 1))

        img = torch.tensor(img)
        img = self.transform(img)
        img = np.transpose(img, (0, 1, 2))  # converting back to CHW format

        return img, torch.tensor([0, -1, -1, -1, -1]).float()

    def __len__(self):
        return self.n_samples


class RoomImgDataset(Dataset):
    def __init__(self, folder_path, transform=None):
        self.folder_path = Path(folder_path)
        self.image_files = sorted(os.listdir(folder_path))
        self.transform = transform

    def __len__(self):
        return len(self.image_files)

    def __getitem__(self, idx):
        img_path = self.folder_path / self.image_files[idx]
        img = cv2.imread(str(img_path))
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

        if self.transform is not None:
            img = self.transform(img)

        return img, torch.tensor([0, -1, -1, -1, -1])


class TenThousandFaceDataSet(Dataset):
    def __init__(self, csv_file, image_dir, transform=None, transform_bbox=None):
        self.data = pd.read_csv(csv_file)
        self.image_dir = Path(image_dir)
        self.transform = transform
        self.transform_bbox = transform_bbox

    def __len__(self):
        return len(self.data)

    def __getitem__(self, idx):
        img_name = os.path.join(
            self.image_dir.joinpath(f"{self.data.iloc[idx, 0]}"))
        image = Image.open(img_name)

        if image.mode != 'RGB':
            image = image.convert('RGB')

        # Извлекаем координаты bbox из CSV файла
        x1, y1, x2, y2 = self.data.iloc[idx, 1:5].values.astype(np.float32)
        width, height = image.size
        # Создаем ограничивающий прямоугольник (bbox)

        bbox = torch.Tensor([1, x1/width, y1/height, x2/width, y2/height])

        if self.transform_bbox is not None:
            items = self.transform_bbox(image=np.transpose(image, (1, 2, 0)), bboxes=[
                                        list(bbox[1:])], class_labels=[1])
            # img = np.transpose(items['image'], (2, 0, 1)) # converting back to HHWC format
            print(items)
            if len(items['bboxes']) > 0:
                bbox = torch.tensor([1] + list(items['bboxes'][0]))
            else:
                # if bbox is too small after the augmentation we drop the bbox
                bbox = torch.tensor([0, -1, -1, -1, -1])

        # Применяем преобразования к изображению (если указаны)
        if self.transform:
            image = self.transform(image)

        return image, bbox


class CelebATriplets(Dataset):
    def __init__(self, images, triplets_path, width, height, transform=None):
        self.images_path = Path(images)
        self.triplets_path = Path(triplets_path)
        self.triplets = pd.read_csv(self.triplets_path)
        self.transform = transform
        self.width = width
        self.height = height

    def __getitem__(self, index):
        triplet = self.triplets[self.triplets.index == index]

        def get_img(image_path):
            img = cv2.imread(str(image_path))
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            img = cv2.resize(img, (self.width, self.height)).astype(np.float32)
            img /= 255.0
            img = np.transpose(img, (2, 0, 1))
            return img

        anc_path = Path(triplet.anchor.values[0])
        pos_path = Path(triplet.pos.values[0])
        neg_path = Path(triplet.neg.values[0])

        anc = get_img(self.images_path.joinpath(anc_path))
        pos = get_img(self.images_path.joinpath(pos_path))
        neg = get_img(self.images_path.joinpath(neg_path))

        # pos_id = triplet.id2.values[0]
        # neg_id = triplet.id3.values[0]
        return [anc, pos, neg]

    def __len__(self):
        return len(self.triplets)


transform_faces = A.Compose([
    A.Rotate(limit=30, p=0.5),
    A.RandomBrightnessContrast(brightness_limit=0.5, contrast_limit=0.5, p=0.5),
    A.Flip(p=0.5),
    A.GaussianBlur(p=0.5)
], bbox_params=A.BboxParams(format='pascal_voc', min_visibility=0.1, label_fields=['class_labels'])) # min_area=1024 min_visibility=0.1


transform = transforms.Compose([
    transforms.RandomCrop((256, 256)),
    transforms.ToTensor(),
    transforms.Resize((img_size, img_size))
])


# --Detection Dataloader--
# Путь к папке с изображениями и CSV файлу
image_dir_for_ten_thousand_dataset = f"{root}data/face-detection-dataset/images"
csv_file_path_for_ten_thousand_dataset = f"{root}data/face-detection-dataset/labels_and_coordinates.csv"

TenThousandFace_dataset = TenThousandFaceDataSet(csv_file=csv_file_path_for_ten_thousand_dataset, image_dir=image_dir_for_ten_thousand_dataset, transform=transform, transform_bbox=None)
ThreeThousandFace_dataset = FacesDataset(image_path, y_labels, transform_faces, None, 256, 256)
dataset_of_backgrounds = BackgroundDataset(backg_image_path, transform, img_size, img_size)
TenThousandFace_dataset = TenThousandFaceDataSet(
    csv_file=csv_file_path_for_ten_thousand_dataset, image_dir=image_dir_for_ten_thousand_dataset, transform=transform, transform_bbox=None)
ThreeThousandFace_dataset = FacesDataset(image_path, y_labels, None, transform)
# dataset_of_backgrounds = BackgroundDataset(backg_image_path, transform, img_size, img_size)

d1 = RoomImgDataset(
    folder_path=f'{root}data/house-rooms-image-dataset/House_Room_Dataset/Bathroom', transform=transform)
d2 = RoomImgDataset(
    folder_path=f'{root}data/house-rooms-image-dataset/House_Room_Dataset/Bedroom', transform=transform)
d3 = RoomImgDataset(
    folder_path=f'{root}data/house-rooms-image-dataset/House_Room_Dataset/Dinning', transform=transform)
d4 = RoomImgDataset(
    folder_path=f'{root}data/house-rooms-image-dataset/House_Room_Dataset/Kitchen', transform=transform)
d5 = RoomImgDataset(
    folder_path=f'{root}data/house-rooms-image-dataset/House_Room_Dataset/Livingroom', transform=transform)

dataset = ConcatDataset(
    [ThreeThousandFace_dataset, TenThousandFace_dataset, d1, d2, d3, d4, d5])

detection_dataloader = DataLoader(
    dataset=dataset, batch_size=batch_size, shuffle=True)


# --FaceId Dataloader--
# celebA dataset
celeb_images = f"{root}data/celeba-face-recognition-triplets/images"
celeb_triplets_csv = f"{root}data/celeba-face-recognition-triplets/triplets.csv"

CelebA_dataset = CelebATriplets(
    celeb_images, celeb_triplets_csv, img_size_recog, img_size_recog)

recognition_dataloader = DataLoader(
    dataset=CelebA_dataset, batch_size=batch_size, shuffle=True)
