import json
import math
import os
import random
from PIL import Image
import torch
from torch.utils.data import Dataset
import torchvision.transforms as transforms
import torchvision.transforms.functional as F
import pandas as pd
import Constants
import unicodedata

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

def filter_text(text, desired_char_set):
    return ''.join([char for char in text if char in desired_char_set])

def remove_accents(input_str):
    # Normalize the string to NFD form and filter out diacritical marks
    nfkd_form = unicodedata.normalize('NFD', input_str)
    # Join characters that are not combining marks (i.e., remove accents)
    return ''.join([c for c in nfkd_form if not unicodedata.combining(c)])

class CustomImageDataset3(Dataset):

    def __init__(self, img_dir, transform=None, train=False):
        self.maxHeight = Constants.desired_size
        self.maxWidth = Constants.desired_size
        self.img_dir = img_dir
        self.transform = transform
        self.train = train
        self.image_filenames = [f for f in os.listdir(img_dir) if os.path.isfile(os.path.join(img_dir, f))]
        self.rotation_transform = RandomRotationWithBBox(angle_range=(-10, 10), p=0.5)

        # Set the appropriate JSON file based on training or test data
        if train:
            annotations_file = "./backend/training_data/TextOCR_0.1_train.json"
            self.csv_file = "./backend/training_data/train-images-boxable-with-rotation.csv"
        else:
            annotations_file = "./backend/training_data/TextOCR_0.1_val.json"
            self.csv_file = "./backend/training_data/train-images-boxable-with-rotation.csv"

        # Load JSON data (images and annotations)
        with open(annotations_file, 'r') as f:
            self.data = json.load(f)

        # Extract image metadata
        self.imgs = self.data['imgs']
        self.anns = self.data.get('anns', {})  # Annotations
        self.img2Anns = self.data.get('imgToAnns', {})  # Image to annotations mapping

        # Create a list of image IDs for iteration
        self.image_ids = list(self.imgs.keys())
        self.df = pd.read_csv(self.csv_file)

        self.samples = []
        for image_id in self.image_ids:
            ann_ids = self.img2Anns.get(image_id, [])
            for ann_id in ann_ids:
                ann = self.anns.get(ann_id)
                if ann and ann['utf8_string'] != '.':
                    ann['utf8_string'] = filter_text(remove_accents(ann['utf8_string']), Constants.char_set)
                    if ann['utf8_string'] != '.' and ann['utf8_string'] != '':
                        self.samples.append((ann_id, ann))

    def __len__(self):
        return len(self.samples)


    def pad_to_target_size(self, image_tensor, target_width, target_height):
        _, height, width = image_tensor.shape

        pad_width = max(0, target_width - width)
        pad_height = max(0, target_height - height)

        PadLeft = random.randint(0, pad_width)
        PadRight = pad_width - PadLeft
        PadTop = random.randint(0, pad_height)
        PadBottom = pad_height - PadTop

        padding = (PadLeft, PadTop, PadRight, PadBottom)

        padded_image_tensor = F.pad(image_tensor, padding, fill=0)

        return padded_image_tensor, padding

    def getScales(self, original_size, new_size):
        old_width, old_height = original_size
        new_width, new_height = new_size

        # Scale factors for resizing
        scale_x = new_width / old_width
        scale_y = new_height / old_height

        return scale_x, scale_y

    def points_to_bbox(self, points):
        """
        Convert a list of points defining a polygon into a bounding box.

        Parameters:
        - points: List of floats where each pair of floats represents x, y coordinates of a vertex.

        Returns:
        - bbox: A list containing [min_x, min_y, max_x, max_y] which defines the bounding box.
        """
        x_coordinates = points[0::2]  # Extract all x coordinates
        y_coordinates = points[1::2]  # Extract all y coordinates

        min_x = min(x_coordinates)
        max_x = max(x_coordinates)
        min_y = min(y_coordinates)
        max_y = max(y_coordinates)

        bbox = [min_x, min_y, max_x, max_y]
        return bbox

    def __getitem__(self, idx):
        try:
            # Retrieve the (image_id, ann) tuple for the current index
            ann_id, ann = self.samples[idx]
            
            # Debug: Print types and values for the first few samples
            if idx < 5:
                print(f"Fetching sample {idx}:")
                print(f"Type of ann_id: {type(ann_id)}, Value: {ann_id}")
                print(f"Type of ann: {type(ann)}, Value: {ann}")
            
            # Ensure image_id is a string
            if not isinstance(ann_id, str):
                raise TypeError(f"Expected ann_id to be a str, but got {type(ann_id)}")
            
            # Construct the full image path
            img_path = os.path.join(self.img_dir, "cropped_images/" + ann_id+".png")
            
            # Load and convert the image to RGB
            image = Image.open(img_path).convert('RGB')

            if self.transform:
                # Apply transformations to the image (assumed to return a tensor)
                image_tensor = self.transform(image)  # Shape: (C, H, W)


            # Extract the text from the annotation
            utf8_string = ann['utf8_string']

            # Skip samples where the text is just "."
            if utf8_string == ".":
                raise ValueError("Text is just a dot '.'")

            # Return the single cropped image and its corresponding text
            return image_tensor, utf8_string

        except Exception as e:
            print(f"Exception occurred in __getitem__: {e}")
            return None




class RandomRotationWithBBox:
    def __init__(self, angle_range=(-10, 10), p=0.5):
        self.angle_range = angle_range
        self.p = p

    def __call__(self, img_tensor, angle=None):
        if angle is None:
            angle = random.uniform(*self.angle_range) if random.random() < self.p else 0
        if angle == 0:
            return img_tensor, angle
        img_tensor = F.rotate(img_tensor, angle)
        return img_tensor, angle

    @staticmethod
    def rotateBBox(imgSize, box, angle):
        if angle == 0:
            return box  # No rotation needed

        (w, h) = imgSize
        x_min, y_min, x_max, y_max = box

        # Define the center of the image
        cx, cy = w / 2, h / 2

        # Define the four corners of the bounding box
        corners = [
            (x_min, y_min),  # top-left
            (x_max, y_min),  # top-right
            (x_min, y_max),  # bottom-left
            (x_max, y_max),  # bottom-right
        ]

        # Convert the angle to radians and calculate cosine and sine
        angle_rad = math.radians(angle)
        cos_a = math.cos(angle_rad)
        sin_a = math.sin(angle_rad)

        # Rotate each corner around the image center
        rotated_corners = []
        for x, y in corners:
            # Translate corner to image center
            x_shifted, y_shifted = x - cx, y - cy

            # Apply rotation matrix
            new_x = x_shifted * cos_a - y_shifted * sin_a + cx
            new_y = x_shifted * sin_a + y_shifted * cos_a + cy
            rotated_corners.append((new_x, new_y))

        # Determine the new bounding box coordinates
        new_x_min = min(c[0] for c in rotated_corners)
        new_y_min = min(c[1] for c in rotated_corners)
        new_x_max = max(c[0] for c in rotated_corners)
        new_y_max = max(c[1] for c in rotated_corners)

        # Output the final rotated bounding box
        rotated_bbox = [new_x_min, new_y_min, new_x_max, new_y_max]

        return rotated_bbox