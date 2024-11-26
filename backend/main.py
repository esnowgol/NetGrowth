import json
import os
import random
from charset_normalizer import detect
import torch
import time
import torch.nn as nn
import torch.optim as optim
import torchvision
import torchvision.transforms as transforms
from torch.utils.data import DataLoader
import torchvision.transforms.functional as F
from GIoULoss import GIoULoss
import DisplayImage
from customDataSet import CustomImageDataset
import torchvision.ops as ops
from transforms import ResizeToMaxDimension
from datetime import datetime
from PIL import Image
from torch.nn.utils.rnn import pad_sequence
import torch.profiler
from CombinedLoss import *
import Constants
import math
from torch.optim.lr_scheduler import SequentialLR, LinearLR, ReduceLROnPlateau
from torch.utils.tensorboard import SummaryWriter
import torch.multiprocessing as mp
import gc
from torch_lr_finder import LRFinder
import torch
import matplotlib.pyplot as plt

#from yolov5 import detect



device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

import torch
import torch.nn as nn

class YOLOv3(nn.Module):
    def __init__(self, num_classes=1):
        super(YOLOv3, self).__init__()
        
        self.num_classes = num_classes
        self.num_anchors = 1  # 3 anchors per detection scale
        self.out_channels = self.num_anchors * (5 + num_classes)

        # Convolution + BatchNorm + LeakyReLU block
        def conv_block(in_channels, out_channels, kernel_size, stride):
            padding = (kernel_size - 1) // 2
            return nn.Sequential(
                nn.Conv2d(in_channels, out_channels, kernel_size, stride, padding, bias=False),
                nn.BatchNorm2d(out_channels),
                nn.LeakyReLU(0.1)
            )

        # Residual block with skip connections
        class ResidualBlock(nn.Module):
            def __init__(self, in_channels, out_channels):
                super().__init__()
                self.block = nn.Sequential(
                    conv_block(in_channels, out_channels // 2, 1, 1),  # 1x1 reduction
                    conv_block(out_channels // 2, out_channels, 3, 1)  # 3x3 expansion
                )

            def forward(self, x):
                return x + self.block(x)  # Skip connection

        # Backbone: Darknet-53
        self.backbone = nn.Sequential(
            conv_block(3, 32, 3, 1),            # Layer 1
            conv_block(32, 64, 3, 2),           # Layer 2
            ResidualBlock(64, 64),              # Layer 3-4

            conv_block(64, 128, 3, 2),          # Layer 5
            ResidualBlock(128, 128),            # Layer 6-7
            ResidualBlock(128, 128),            # Layer 8-9

            conv_block(128, 256, 3, 2),         # Layer 10
            ResidualBlock(256, 256),            # Layer 11-12
            ResidualBlock(256, 256),            # Layer 13-14
            ResidualBlock(256, 256),            # Layer 15-16
            ResidualBlock(256, 256),            # Layer 17-18
            ResidualBlock(256, 256),            # Layer 19-20
            ResidualBlock(256, 256),            # Layer 21-22
            ResidualBlock(256, 256),            # Layer 23-24

            conv_block(256, 512, 3, 2),         # Layer 25
            ResidualBlock(512, 512),            # Layer 26-27
            ResidualBlock(512, 512),            # Layer 28-29
            ResidualBlock(512, 512),            # Layer 30-31
            ResidualBlock(512, 512),            # Layer 32-33
            ResidualBlock(512, 512),            # Layer 34-35
            ResidualBlock(512, 512),            # Layer 36-37
            ResidualBlock(512, 512),            # Layer 38-39

            conv_block(512, 1024, 3, 2),        # Layer 40
            ResidualBlock(1024, 1024),          # Layer 41-42
            ResidualBlock(1024, 1024),          # Layer 43-44
            ResidualBlock(1024, 1024),          # Layer 45-46
            ResidualBlock(1024, 1024)           # Layer 47-48
        )

        # Neck: Feature Pyramid Network (FPN)
        self.neck = nn.Sequential(
            conv_block(1024, 512, 1, 1),
            conv_block(512, 1024, 3, 1),
            conv_block(1024, 512, 1, 1)
        )

        # Neck: Feature Pyramid Network (FPN) with Upsampling
        self.upsample1 = nn.Upsample(scale_factor=2, mode='nearest')  # For medium objects
        self.upsample2 = nn.Upsample(scale_factor=2, mode='nearest')  # For small objects

        self.conv1 = conv_block(512, 512, 1, 1)  # Conv after upsample1
        self.conv2 = conv_block(256, 256, 1, 1)   # Conv after upsample2

        # Detection Layers
        self.detect1 = nn.Conv2d(512, self.out_channels, 1)  # Large objects
        self.detect2 = nn.Conv2d(512, self.out_channels, 1)  # Medium objects
        self.detect3 = nn.Conv2d(256, self.out_channels, 1)  # Small objects

    def forward(self, x):
        # Pass input through the backbone
        backbone_out = self.backbone(x)

        # Pass through the neck
        neck_out = self.neck(backbone_out)

        # Detect objects at three scales
        #out1 = self.detect1(neck_out)  # Large-scale detection
        medium_feature = self.upsample1(neck_out)  # [B, 1024, 16, 16]
        medium_feature = self.conv1(medium_feature)    # [B, 512, 16, 16]
        out2 = self.detect2(medium_feature) 
        #out3 = self.detect3(neck_out)  # Small-scale detection

        #small_feature = self.upsample2(medium_feature)  # [B, 512, 32, 32]
        #small_feature = self.conv2(small_feature)       # [B, 256, 32, 32]
        #out3 = self.detect3(small_feature)  

        return out2


class BoundingBoxCnn(nn.Module):
    def __init__(self, max_boxes, anchor_boxes, B=5):
        super().__init__()
        self.anchor_boxes = anchor_boxes
        self.max_boxes = max_boxes
        self.maxWidth = Constants.desired_size
        self.maxHeight = Constants.desired_size
        self.relu = nn.ReLU()
        self.sigmoid = nn.Sigmoid()
        self.pool = nn.MaxPool2d(2,2)
        self.B = B
        self.conv1 = nn.Conv2d(3, 32, kernel_size=3, padding=1, stride=1)  # 512 -> 512
        self.bn1 = nn.BatchNorm2d(32)
        self.conv2 = nn.Conv2d(32, 64, kernel_size=3, padding=1, stride=1)  # 512 -> 256
        self.bn2 = nn.BatchNorm2d(64)
        self.conv3 = nn.Conv2d(64, 128, kernel_size=3, padding=1, stride=1)  # 256 -> 256
        self.bn3 = nn.BatchNorm2d(128)
        self.conv4 = nn.Conv2d(128, 256, kernel_size=3, padding=1, stride=1)  # 256 -> 128
        self.bn4 = nn.BatchNorm2d(256)
        self.conv5 = nn.Conv2d(256, 256, kernel_size=3, padding=1, stride=1)  # 128 -> 128
        self.bn5= nn.BatchNorm2d(256)
        self.conv6 = nn.Conv2d(256, 256, kernel_size=3, padding=1, stride=1)  # 128 -> 64
        self.bn6 = nn.BatchNorm2d(256)
        self.conv7 = nn.Conv2d(256, 256, kernel_size=3, padding=1, stride=1)  # 64 -> 32
        self.bn7 = nn.BatchNorm2d(256)
        self.conv8 = nn.Conv2d(256, 256, kernel_size=3, padding=1, stride=1)  # 32 -> 16
        self.bn8 = nn.BatchNorm2d(256)
        self.convFinal = nn.Conv2d(256, 5*B, kernel_size=1)  # Predict 4 values (x, y, x2, y2, confidence) for each bbox

    def forward(self, x):#thrt
        #print(str(x.shape))
        x = self.relu(self.bn1(self.conv1(x)))
        #print(str(x.shape))
        x = self.relu(self.bn2(self.conv2(x)))
        #x = self.pool(x)
        x = self.relu(self.bn3(self.conv3(x)))
        #print(str(x.shape))
        x = self.relu(self.bn4(self.conv4(x)))
        x = self.pool(x)
        #print(str(x.shape))
        x = self.relu(self.bn5(self.conv5(x)))
        #print(str(x.shape))
        x = self.relu(self.bn6(self.conv6(x)))
        x = self.pool(x)
        x = self.relu(self.bn7(self.conv7(x)))
        x = self.pool(x)
        x = self.relu(self.bn8(self.conv8(x)))
        x = self.pool(x)
        #print(str(x.shape))
        x = self.convFinal(x)  # [batch_size, max_boxes * 4, H, W]
        #print(str(x.shape))
        x = x.view(-1, x.shape[2], x.shape[3], self.B, 5)
        return x
    

max_boxes = 0

def train2(model, loader, criterion, optimizer):
    model.train()
    running_loss = 0.0
    correct = 0
    total = 0
    iou_threshold = 0.5  # IoU threshold for determining a correct prediction

    for images, bboxes in loader:
        images = images.to(device)
        bboxes = bboxes.to(device)
        outputs = model(images)

        # Calculate loss between predicted and ground truth bounding boxes
        loss_bbox = criterion(outputs, bboxes)

        optimizer.zero_grad()
        loss_bbox.backward()
        optimizer.step()

        # Accumulate loss
        running_loss += loss_bbox.item() * images.size(0)
        
        # Loop over each image in the batch
        for i in range(outputs.size(0)):  # Iterate over batch size
            predicted_boxes = outputs[i]  # Predicted boxes for this image
            ground_truth_boxes = bboxes[i]  # Ground truth boxes for this image
            
            # Compute IoU for this image's predicted and ground truth boxes
            iou_matrix = ops.box_iou(predicted_boxes, ground_truth_boxes)
            
            # For each ground truth box, find the predicted box with the highest IoU
            max_iou_per_gt, _ = torch.max(iou_matrix, dim=0)  # Max IoU for each ground truth box
            
            # Count ground truth boxes that have a matching predicted box with IoU > threshold
            correct_boxes = (max_iou_per_gt > iou_threshold).sum().item()
            correct += correct_boxes
            
            # The total number of ground truth boxes (for accuracy calculation)
            total += ground_truth_boxes.size(0)

    epoch_loss = running_loss / total
    epoch_acc = 100 * correct / total  # Calculate accuracy as a percentage
    return epoch_loss, epoch_acc


def evaluate2(model, loader, criterion):
    model.eval()
    running_loss = 0.0
    correct=0
    total=0
    iou_threshold = 0.5  # IoU threshold for determining a correct prediction
    with torch.no_grad():
        for images, bboxes in loader:
            images = images.to(device)
            bboxes = bboxes.to(device)
            outputs = model(images)

            # Calculate loss between predicted and ground truth bounding boxes
            loss_bbox = criterion(outputs, bboxes)

            optimizer.zero_grad()
            optimizer.step()

            # Accumulate loss
            running_loss += loss_bbox.item() * images.size(0)
            
            # Loop over each image in the batch
            for i in range(outputs.size(0)):  # Iterate over batch size
                predicted_boxes = outputs[i]  # Predicted boxes for this image
                ground_truth_boxes = bboxes[i]  # Ground truth boxes for this image
                
                # Compute IoU for this image's predicted and ground truth boxes
                iou_matrix = ops.box_iou(predicted_boxes, ground_truth_boxes)
                
                # For each ground truth box, find the predicted box with the highest IoU
                max_iou_per_gt, _ = torch.max(iou_matrix, dim=0)  # Max IoU for each ground truth box
                
                # Count ground truth boxes that have a matching predicted box with IoU > threshold
                correct_boxes = (max_iou_per_gt > iou_threshold).sum().item()
                correct += correct_boxes
                
                # The total number of ground truth boxes (for accuracy calculation)
                total += ground_truth_boxes.size(0)

        epoch_loss = running_loss / total
        epoch_acc = 100 * correct / total  # Calculate accuracy as a percentage
        return epoch_loss, epoch_acc
    



def extract_top_bboxes2(pred_tensor, max_boxes=35):
    """
    Extracts the top-k bounding boxes based on confidence, padded to max_boxes size if necessary.

    Args:
        pred_tensor (Tensor): Output tensor of shape [batch_size, 16, 16, 2, 5].
        max_boxes (int): Number of bounding boxes to keep.

    Returns:
        Tensor: Bounding boxes of shape [batch_size, max_boxes, 4].
    """
    batch_size, grid_size, _, num_boxes, _ = pred_tensor.shape
    cell_size = Constants.desired_size / grid_size  # Size of each grid cell in pixels

    all_bboxes = []  # Store top-k bounding boxes for each image

    for batch_idx in range(batch_size):
        bboxes = []

        # Iterate over each grid cell
        for i in range(grid_size):
            for j in range(grid_size):
                # For each box in the cell
                for b in range(num_boxes):
                    x1, y1, x2, y2, conf = pred_tensor[batch_idx, i, j, b]

                    # Convert grid coordinates to image coordinates
                    x1 = (j + x1) * cell_size  # Horizontal offset
                    y1 = (i + y1) * cell_size  # Vertical offset
                    x2 = (j + x2) * cell_size
                    y2 = (i + y2) * cell_size

                    # Append (x1, y1, x2, y2, confidence) to list
                    bboxes.append((x1, y1, x2, y2, conf))

        # Sort all bounding boxes by confidence in descending order
        bboxes = sorted(bboxes, key=lambda x: x[4], reverse=True)

        # Keep only the top-k bounding boxes
        top_bboxes = [bbox[:4] for bbox in bboxes[:max_boxes]]  # List of [x1, y1, x2, y2]
        confidences = [bbox[4] for bbox in bboxes[:max_boxes]]  # List of confidence scores

        # Pad with [0, 0, 0, 0] if fewer than max_boxes boxes are found
        while len(top_bboxes) < max_boxes:
            top_bboxes.append([0.0, 0.0, 0.0, 0.0])
            confidences.append(0.0)

        all_bboxes.append(top_bboxes)

    # Convert the result to a tensor of shape [batch_size, max_boxes, 4]
    return torch.tensor(all_bboxes, dtype=torch.float32, device=pred_tensor.device, requires_grad=True), torch.tensor(confidences, dtype=torch.float32, device=pred_tensor.device, requires_grad=True)


def get_nth_image(data_loader, n):
    for i, (images, bboxes, paths) in enumerate(data_loader):
        bboxes = torch.cat(bboxes, dim=1)[n]
        mask = (bboxes != 0).any(dim=1) 
        return images[n], bboxes[mask], paths[n]  # Return the nth image and label
    

class ClampBoxCoords(torch.autograd.Function):
    @staticmethod
    def forward(ctx, inputs, min_val, max_val):
        ctx.save_for_backward(inputs)
        ctx.min_val = min_val
        ctx.max_val = max_val
        return inputs.clamp(min=min_val, max=max_val)

    @staticmethod
    def backward(ctx, grad_output):
        inputs, = ctx.saved_tensors
        grad_input = grad_output.clone()
        grad_input[inputs < ctx.min_val] = 0
        grad_input[inputs > ctx.max_val] = 0
        return grad_input, None, None


def generate_grid_indices(height, width, device):
    """
    Generate grid indices for each cell in the grid.
    """
    grid_y, grid_x = torch.meshgrid(torch.arange(height), torch.arange(width), indexing='ij')
    return grid_x.to(device).float(), grid_y.to(device).float()



def filter_confidences(pred_boxes, confidences, threshold=0.5):
    """
    Filter predicted boxes and their confidences based on a confidence threshold.
    
    Parameters:
    - pred_boxes: Tensor of predicted boxes [N, 4].
    - confidences: Tensor of confidence scores [N, 1].
    - threshold: Float, the minimum confidence score to keep a box.
    
    Returns:
    - filtered_boxes: Tensor of filtered boxes.
    - filtered_confidences: Tensor of filtered confidence scores, retaining the original shape [M, 1].
    """
    # Create a mask for all elements that exceed the threshold
    mask = (confidences >= threshold).squeeze()  # Squeeze to remove extra dimension for comparison, result is [N]

    # Use the mask to filter out boxes and confidences
    filtered_boxes = pred_boxes[mask]  # Apply the mask to the boxes
    filtered_confidences = confidences[mask]  # Keep the result as [M, 1] where M is the number of elements that passed the filter

    return filtered_boxes, filtered_confidences

def apply_nms(pred_boxes, confidences, iou_threshold=0.5):
    """
    Apply Non-Maximum Suppression to reduce overlapping boxes and ensure the confidence tensor's shape is maintained as [N, 1].

    Parameters:
    - pred_boxes: Tensor of filtered predicted boxes [N, 4].
    - confidences: Tensor of filtered confidence scores [N, 1].
    - iou_threshold: Float, the IoU threshold for filtering overlaps.

    Returns:
    - kept_boxes: Tensor of boxes kept after NMS.
    - kept_confidences: Tensor of confidence scores of kept boxes, shape [K, 1] where K is the number of boxes kept.
    """
    # Apply NMS using the confidences squeezed to one dimension for proper NMS functionality
    keep_indices = torchvision.ops.nms(pred_boxes, confidences.squeeze(1), iou_threshold)

    # Index the results with keep_indices. The result for boxes is straightforward.
    kept_boxes = pred_boxes[keep_indices]

    # For confidences, ensure the output shape is [K, 1] by using keep_indices
    kept_confidences = confidences[keep_indices]

    return kept_boxes, kept_confidences


def train(model, loader, criterion, optimizer, optionalLoader=None):
    model = model.to(device)
    model.train()  # Set the model to training mode
    running_loss = 0.0
    total = 0
    total_iou = 0.0
    i = 0
    progress = [5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100]


    for batch_idx, (images, bboxes, _) in enumerate(loader):
        data_time = time.time() - start_time
        # Forward pass: compute model outputs (bounding box coordinates)
        i+=1

        if (batch_idx == 1 or batch_idx % 10 == 0) and True:
            n = 0
            image, bbox, path = get_nth_image(optionalLoader, n)
            image = image.to(device)
            image = image.unsqueeze(0)
            bbox = bbox.to(device)
            bbox = bbox.unsqueeze(0)
            all_pred_coords = None
            if True:
                output = model(image)
                all_pred_coords = []
                all_pred_confidences = []
                for i in range(len(output)):
                    output[i] = output[i][..., :5]
                    output[i] = postprocess_yolo_output(output[i],loaded_anchor_boxes[(i * 3):((i + 1) * 3)])
                    output[i] = output[i].view(1, -1, 5)  # N = num_anchors * grid_h * grid_w

                    output[i][..., :4] = yolo_to_corners_batches(output[i][..., :4])
                
                    #bbox, output, c = filter_and_trim_boxes(output, bbox)
                    pred_boxes = output[i].reshape(1, -1, 5)  # N = num_anchors * grid_h * grid_w
                    pred_coords = pred_boxes[..., :4]  # [batch_size, N, 4]
                    pred_confidences = pred_boxes[..., 4]  # [batch_size, N]
                    pred_confidences = pred_confidences.view(-1, 1)

                    #pred_coords =  #yolo_to_corners(pred_coords.squeeze(0))
                    pred_coords, pred_confidences = filter_confidences(pred_coords.squeeze(0), pred_confidences)
                    #pred_coords, pred_confidences = apply_nms(pred_coords, pred_confidences)

                    all_pred_coords.append(pred_coords)
                    all_pred_confidences.append(pred_confidences)

                # Concatenate all the coordinates and confidences along the first dimension
                all_pred_coords = torch.cat(all_pred_coords, dim=0)  # Shape: (total_boxes, 4)
                all_pred_confidences = torch.cat(all_pred_confidences, dim=0)  # Shape: (total_boxes, 1)

            all_pred_coords, all_pred_confidences = apply_nms(all_pred_coords, all_pred_confidences, 0.7)
            all_pred_coords, all_pred_confidences = apply_nms(all_pred_coords, all_pred_confidences, 0.3)

            bbox = fix_box_coordinates(bbox)
            bbox = clipBoxes(bbox)
            mean=torch.tensor([0.3490, 0.3219, 0.2957]).to(device)
            std=torch.tensor([0.2993, 0.2850, 0.2735]).to(device)
            un_normalized_img = image * std[:, None, None] + mean[:, None, None]

            # Convert to PIL for display
            rotated_img = transforms.ToPILImage()(image.squeeze(0).clamp(0, 1))
            #bbox.squeeze()
            DisplayImage.draw_bounding_boxes(rotated_img, None, all_pred_coords, epoch+1, batch_idx, n, BBtransform)
            print("Image taken",epoch+1, batch_idx)

        #if torch.isnan(images).any() or torch.isinf(images).any():
            #print(f"NaN or Inf detected in images at batch {batch_idx}")
        #if torch.isnan(bboxes).any() or torch.isinf(bboxes).any():
            #print(f"NaN or Inf detected in bounding boxes at batch {batch_idx}")
        percentage = (batch_idx / len(loader)) * 100
        # This checks if the current percentage point is approximately a multiple of 5
        if len(progress) > 0 and int(percentage) >= progress[0]:  # Ensuring that it checks every 5% increment
                print(f"{progress.pop(0)}% ", end="", flush=True)
        images = images.to(device)
        for i in range(len(bboxes)):
            bboxes[i] = bboxes[i].to(device)
        outputs = model(images)
        #outputs[1] = outputs[1][..., :5]

        #writer.add_scalar('Avg output', outputs[1].mean(), epoch * len(train_loader) + batch_idx)
        if False:
            print("")
        # Compute the loss between predicted and true bounding box coordinates
        loss = criterion(outputs, bboxes, writer, epoch * len(train_loader) + batch_idx)

        # Log training loss for this batch to TensorBoard
        current_lr = optimizer.param_groups[0]['lr']
        writer.add_scalar('Learning Rate', current_lr, epoch * len(train_loader) + batch_idx)
        
        # Backward pass and optimization
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        # Accumulate the loss for the current batch
        running_loss += loss.item() * images.size(0)
        total += images.size(0)
        total_time = time.time() - start_time
        print(str(running_loss/total), f' Data loading time: {data_time:.4f}s, Total batch time: {total_time:.4f}s')
        # Calculate IoU for the batch
        #outputs_trimmed = outputs[1][..., :4]
        #outputs_flat = outputs_trimmed.view(-1, 4)
        #avg_batch_iou  = 0#calculate_valid_iou(outputs_flat, bboxes.view(-1,4))  # IoU between predicted and true boxes
        #avg_batch_iou = batch_iou.diag().mean().item()  # Average IoU for the batch
        #writer.add_scalar('avg iou', avg_batch_iou, epoch * len(train_loader) + batch_idx)

        #total_iou += avg_batch_iou * images.size(0)
        start_time = time.time()
    print()
    # Average loss and IoU per epoch
    #for name, param in model.named_parameters():
        #if param.grad is not None:
            #print(f"{name}: {param.grad.norm()}")
    epoch_loss = running_loss / total
    epoch_iou = 100 * (total_iou / total)
    return epoch_loss, epoch_iou


def evaluate(model, loader, criterion):
    model.eval()  # Set the model to eval mode
    running_loss = 0.0
    total = 0
    total_iou=0.0
    i=0
    iou_threshold=0.5
    progress = [5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100]
    with torch.no_grad():
        for images, bboxes, _ in loader:
            # Forward pass: compute model outputs (bounding box coordinates)
            i+=1
            percentage = (i / len(loader)) * 100
            # This checks if the current percentage point is approximately a multiple of 5
            if len(progress) > 0 and int(percentage) >= progress[0]:  # Ensuring that it checks every 5% increment
                print(f"{progress.pop(0)}% ", end="", flush=True)
            images = images.to(device)
            bboxes = bboxes.to(device)
            outputs = model(images)

            if False:
                print("")
            # Compute the loss between predicted and true bounding box coordinates
            loss = criterion(outputs, bboxes, writer)

            # Accumulate the loss for the current batch
            running_loss += loss.item() * images.size(0)
            total += images.size(0)


            avg_batch_iou  = 0#calculate_valid_iou(outputs_flat, bboxes_flat)  # IoU between predicted and true boxes
            #avg_batch_iou = batch_iou.diag().mean().item()  # Average IoU for the batch

            total_iou += avg_batch_iou * images.size(0)

        # Average loss and IoU per epoch
        epoch_loss = running_loss / total
        epoch_iou = 100 * (total_iou / total)
        return epoch_loss, epoch_iou

def get_mean_std(loader):
    mean = 0.0
    std = 0.0
    total_images_count = 0
    for images, _ in loader:
        batch_samples = images.size(0)  # Batch size (number of images in batch)
        images = images.view(batch_samples, images.size(1), -1)  # Reshape to [batch_size, channels, pixels]
        mean += images.mean(2).sum(0)
        std += images.std(2).sum(0)
        total_images_count += batch_samples

    mean /= total_images_count
    std /= total_images_count
    return mean, std

def get_mean_std_RGB(loader):
    mean = torch.zeros(3)  # Initialize mean for 3 channels: R, G, B
    std = torch.zeros(3)   # Initialize std for 3 channels: R, G, B
    total_images_count = 0
    
    for images, _ in loader:
        batch_samples = images.size(0)  # Batch size (number of images in the batch)
        images = images.view(batch_samples, images.size(1), -1)  # Reshape to [batch_size, channels, pixels]
        
        # Calculate mean and std for each channel (R, G, B)
        mean += images.mean(2).sum(0)
        std += images.std(2).sum(0)
        
        total_images_count += batch_samples

    # Normalize mean and std over the entire dataset
    mean /= total_images_count
    std /= total_images_count
    
    return mean, std

def get_mean_std_RGB2(loader):
    channels_sum = torch.zeros(3)  # For RGB, we need a mean/std for each of the 3 channels
    channels_squared_sum = torch.zeros(3)
    total_images_count = 0
    
    for images, _ in loader:
        batch_samples = images.size(0)  # Batch size (number of images in batch)
        images = images.view(batch_samples, images.size(1), -1)  # Reshape to [batch_size, channels, pixels]
        
        channels_sum += images.mean(2).sum(0)  # Sum the means for each channel
        channels_squared_sum += (images ** 2).mean(2).sum(0)  # Sum of squares for each channel
        total_images_count += batch_samples
    
    # Calculate mean and std for each channel
    mean = channels_sum / total_images_count
    std = torch.sqrt((channels_squared_sum / total_images_count) - (mean ** 2))
    
    return mean, std


def calculate_valid_iou(pred_boxes, true_boxes):
    # Create a mask to ignore [0, 0, 0, 0] padded boxes
    #valid_mask = (true_boxes[:, 2] > true_boxes[:, 0]) & (true_boxes[:, 3] > true_boxes[:, 1])

    # Filter out all-zero ground truth boxes
    valid_mask = (true_boxes.sum(dim=1) > 0)

    # Apply the mask to both predictions and ground truth
    pred_boxes = pred_boxes[valid_mask]
    true_boxes = true_boxes[valid_mask]

    if pred_boxes.size(0) == 0 or true_boxes.size(0) == 0:
        return 0.0  # If no valid boxes, return IoU of 0

    # Calculate IoU only for valid bounding boxes
    iou_matrix = torchvision.ops.box_iou(pred_boxes, true_boxes)
    diag_iou = iou_matrix.diag()  # Get the IoU for corresponding boxes

    return diag_iou.mean().item()  # Return the average IoU

class WarmupScheduler:
    def __init__(self, optimizer, warmup_steps, lr_sequence):
        self.optimizer = optimizer
        self.warmup_steps = warmup_steps
        self.lr_sequence = lr_sequence
        self.step_num = 0

    def step(self):
        if self.step_num < self.warmup_steps:
            lr = self.lr_sequence[self.step_num]
            for param_group in self.optimizer.param_groups:
                param_group['lr'] = lr
            print(f"Warmup Epoch {self.step_num + 1}: Learning rate set to {lr}")
            self.step_num += 1
        else:
            # Return False to indicate warmup is complete
            return False
        return True


class SquarePad:
    def __call__(self, image):
        w, h = image.size
        max_wh = max(w, h)
        padding = [(max_wh - w) // 2, (max_wh - h) // 2, (max_wh - w) - (max_wh - w) // 2, (max_wh - h) - (max_wh - h) // 2]
        return F.pad(image, padding, 0, 'constant')



def collate_fn(batch):
    images, bboxes = zip(*batch)
    images = torch.stack(images, dim=0)

    # Find the max number of bounding boxes in the batch
    max_boxes = max([len(bbox) for bbox in bboxes])

    # Pad bounding boxes to match the largest number of boxes in the batch
    padded_bboxes = [torch.cat([bbox, torch.zeros(max_boxes - len(bbox), 4)], dim=0) for bbox in bboxes]

    bboxes = torch.stack(padded_bboxes, dim=0)

    return images, bboxes


def compute_max_boxes(dataloader):
    max_boxes = 0
    for _, bboxes in dataloader:
        # Assuming bboxes is a tensor of shape (batch_size, num_boxes, box_dimensions)
        # We check the size of the second dimension (num_boxes)
        for bbox_tensor in bboxes:
            num_boxes = bbox_tensor.shape[0]  # Get the number of boxes in this image
            if num_boxes > max_boxes:
                max_boxes = num_boxes
    return max_boxes

def custom_collate_fn(batch):
    images, bboxes, paths = zip(*batch)
    
    # Stack images directly (assuming they are all the same size)
    images = torch.stack(images, dim=0)
    
    # Prepare lists to hold padded bounding boxes for each scale
    small_scale_boxes = []
    medium_scale_boxes = []
    large_scale_boxes = []
    
    # Find the maximum number of boxes in each scale across the batch
    max_small_boxes = max(b[0].shape[0] for b in bboxes)
    max_medium_boxes = max(b[1].shape[0] for b in bboxes)
    max_large_boxes = max(b[2].shape[0] for b in bboxes)
    
    # Pad each scale's bounding boxes and store in the respective list
    for b in bboxes:
        # Pad small scale boxes
        padded_small = torch.cat([b[0], torch.zeros(max_small_boxes - b[0].shape[0], 4)], dim=0) if b[0].shape[0] < max_small_boxes else b[0]
        small_scale_boxes.append(padded_small)
        
        # Pad medium scale boxes
        padded_medium = torch.cat([b[1], torch.zeros(max_medium_boxes - b[1].shape[0], 4)], dim=0) if b[1].shape[0] < max_medium_boxes else b[1]
        medium_scale_boxes.append(padded_medium)
        
        # Pad large scale boxes
        padded_large = torch.cat([b[2], torch.zeros(max_large_boxes - b[2].shape[0], 4)], dim=0) if b[2].shape[0] < max_large_boxes else b[2]
        large_scale_boxes.append(padded_large)
    
    # Stack each scale's bounding boxes along the batch dimension
    small_scale_boxes = torch.stack(small_scale_boxes, dim=0)#.to(device)
    medium_scale_boxes = torch.stack(medium_scale_boxes, dim=0)#.to(device)
    large_scale_boxes = torch.stack(large_scale_boxes, dim=0)#.to(device)
    
    # Combine scales into a single tensor of shape (batch_size, 3, max_boxes, 4)
    bboxes = [small_scale_boxes, medium_scale_boxes, large_scale_boxes]
    
    return images, bboxes, paths




def pad_to_target_size(image_tensor, target_width, target_height):
    # Get the current tensor dimensions (assuming shape is [C, W, H])
    _, height, width = image_tensor.shape
    
    # Calculate padding needed for both width and height
    pad_width = max(0, target_width - width)
    pad_height = max(0, target_height - height)
    
    # Padding is applied as (top, right, bottom, left)
    PadLeft = random.randint(0, pad_width)
    PadRight = pad_width - PadLeft
    PadTop = random.randint(0, pad_height)
    PadBottom = pad_height - PadTop



    #padding = (pad_width // 2, pad_height // 2, pad_width - pad_width // 2, pad_height - pad_height // 2)
    #padding = (pad_height // 2, pad_width // 2, pad_height - pad_height // 2, pad_width - pad_width // 2)
    padding = (PadLeft, PadRight, PadTop, PadBottom)
    
    # Apply padding using F.pad (for tensors), padding must be in (left, right, top, bottom) order
    padded_image_tensor = F.pad(image_tensor, padding, value=0)


    return padded_image_tensor, padding

def getScales(original_size, new_size):
    old_width, old_height = original_size
    new_width, new_height = new_size
    
    # Scale factors for resizing
    scale_x = new_width / old_width
    scale_y = new_height / old_height
    
    return scale_x, scale_y






epoch = 0
if __name__ == "__main__":
            
            means = [0.3490, 0.3219, 0.2957]
            stds=[0.2993, 0.2850, 0.2735]
            BBtransform = transforms.Compose([
                ResizeToMaxDimension(max_dim=Constants.desired_size),  # Resize based on max dimension while maintaining aspect ratio
                transforms.ToTensor(),
                transforms.Normalize(mean=means, std=stds)
            ])

            image_path = input("Please enter the image path: ")

            # Load the image
            ogImage = Image.open(image_path.strip('"'))#remove quotes from sides of path if included
            oldSize = (ogImage.height, ogImage.width)
            image = BBtransform(ogImage)
            newSize = (image.shape[1], image.shape[2])
            scale_y, scale_x  = getScales(oldSize, newSize)
            image, (adjustX1, adjustY1, adjustX2, adjustY2)  = pad_to_target_size(image, Constants.desired_size, Constants.desired_size)
            with open('anchor_boxes.json', 'r') as file:
                loaded_anchor_boxes = json.load(file)
                loaded_anchor_boxes = torch.tensor(loaded_anchor_boxes, dtype=torch.float32)




            loaded_anchor_boxes = loaded_anchor_boxes.to(device)

            cnn_model = torch.hub.load('ultralytics/yolov5', 'yolov5s', pretrained=False).to(device)


            # Access the Detect layer
            detect_layer = cnn_model.model[-1]

            # Ensure it's configured with the correct number of anchors and outputs
            detect_layer.nc = 1  # 1 class (text)
            detect_layer.na = Constants.num_anchor_boxes  # 5 anchors
            detect_layer.no = 6  # 4 bbox + 1 confidence + 1 class = 6 outputs per anchor

            # Apply the custom anchors to the Detect layer
            detect_layer.anchors = loaded_anchor_boxes

            # Update the Conv2d layers in the Detect module to output 30 channels
            for i, layer in enumerate(detect_layer.m):
                in_channels = layer.in_channels
                detect_layer.m[i] = torch.nn.Conv2d(in_channels, Constants.num_anchor_boxes * 6, kernel_size=1)


            max_norm = 5

            checkpoint = torch.load("model_checkpoint154.pth", map_location=device)
            cnn_model.load_state_dict(checkpoint['model_state_dict'])






            image = image.to(device)
            image = image.unsqueeze(0)

            all_pred_coords = None
            if True:
                output = cnn_model(image)
                all_pred_coords = []
                all_pred_confidences = []
                for i in range(len(output)):
                    output[i] = output[i][..., :5]
                    output[i] = postprocess_yolo_output(output[i],loaded_anchor_boxes[(i * 3):((i + 1) * 3)])
                    output[i] = output[i].view(1, -1, 5)  # N = num_anchors * grid_h * grid_w

                    output[i][..., :4] = yolo_to_corners_batches(output[i][..., :4])
                
                    #bbox, output, c = filter_and_trim_boxes(output, bbox)
                    pred_boxes = output[i].reshape(1, -1, 5)  # N = num_anchors * grid_h * grid_w
                    pred_coords = pred_boxes[..., :4]  # [batch_size, N, 4]
                    pred_confidences = pred_boxes[..., 4]  # [batch_size, N]
                    pred_confidences = pred_confidences.view(-1, 1)

                    #pred_coords =  #yolo_to_corners(pred_coords.squeeze(0))
                    pred_coords, pred_confidences = filter_confidences(pred_coords.squeeze(0), pred_confidences)
                    #pred_coords, pred_confidences = apply_nms(pred_coords, pred_confidences)

                    all_pred_coords.append(pred_coords)
                    all_pred_confidences.append(pred_confidences)

                # Concatenate all the coordinates and confidences along the first dimension
                all_pred_coords = torch.cat(all_pred_coords, dim=0)  # Shape: (total_boxes, 4)
                all_pred_confidences = torch.cat(all_pred_confidences, dim=0)  # Shape: (total_boxes, 1)

            all_pred_coords, all_pred_confidences = apply_nms(all_pred_coords, all_pred_confidences, 0.7)
            all_pred_coords, all_pred_confidences = apply_nms(all_pred_coords, all_pred_confidences, 0.3)

            mean=torch.tensor(means).to(device)
            std=torch.tensor(stds).to(device)
            un_normalized_img = image * std[:, None, None] + mean[:, None, None]

            # Convert to PIL for display
            rotated_img = transforms.ToPILImage()(image.squeeze(0).clamp(0, 1))
            #bbox.squeeze()
            path = DisplayImage.draw_bounding_boxes(rotated_img, None, all_pred_coords, 0,0, 0, BBtransform)
            print("Image of bounding boxes taken, stored in path:")
            print(path)

            #"C:\Users\evans\Desktop\Semester Projects\NetGrowth\backend\training_data\test\1c908cd87852e244.jpg"

            CRNNtransform = transforms.Compose([
            transforms.ToTensor(),
            #transforms.Normalize(mean=[0.3490, 0.3219, 0.2957], std=[0.2993, 0.2850, 0.2735])
        ])



            bboxes = []
            for bbox in all_pred_coords:
                thisImage = ogImage.copy()
                scale_y, scale_x  = getScales(newSize, oldSize)
                thisBox = [0,0,0,0]
                thisBox[0] = (bbox[0]*scale_x) - adjustX1
                thisBox[1] = (bbox[1]*scale_y) - adjustY1
                thisBox[2] = (bbox[2]*scale_x) - adjustX1
                thisBox[3] = (bbox[3]*scale_y) - adjustY1
                bboxes.append(thisBox)

            all_pred_coords = torch.cat(bboxes, dim=0)


            path = DisplayImage.draw_bounding_boxes(rotated_img, None, all_pred_coords, 0,1, 0, BBtransform)
            print("Image of bounding boxes taken, stored in path:")
            print(path)















            del cnn_model
            gc.collect()
            #test_loss, test_acc = evaluate(cnn_model, test_loader, criterion)
            #print("\nFinal Evaluation on Test Set:")
            #print(f"Test Loss: {test_loss:.4f}, Test Accuracy: {test_acc:.2f}%")