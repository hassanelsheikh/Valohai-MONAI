"""
Module for running inference with liver segmentation model.
"""
import argparse
import os

import nibabel as nib
import numpy as np
import torch
import valohai
from monai.data import DataLoader, Dataset, decollate_batch
from monai.inferers import sliding_window_inference
from monai.transforms import AsDiscreted, Compose, Invertd, SaveImaged

from utils.model import get_model_network
from utils.transforms import get_transforms
from utils.visualizations import visualize_preprocessed_image


def parse_args():
    parser = argparse.ArgumentParser(description='Train liver segmentation model')
    parser.add_argument('--in_channels', type=int, default=1)
    parser.add_argument('--out_channels', type=int, default=3)
    parser.add_argument('--num_res_units', type=int, default=2)
    parser.add_argument('--channels', type=lambda s: list(map(int, s.split(','))))
    return parser.parse_args()


def run_inference(ckpt, input_image_path, output_path, model):
    """
    Run inference on a single liver image.

    Args:
        ckpt (str): Path to the model checkpoint
        input_image_path (str): Path to input image
        output_path (str): Path to save segmentation mask
    """
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    # Initialize model
    model.load_state_dict(torch.load(ckpt, map_location=device))
    model.to(device)
    model.eval()

    # Define transforms
    inference_transform = get_transforms('inference')

    # Attach output dir to post-transforms
    post_transforms = Compose([
        Invertd(
            keys="pred",
            transform=inference_transform,
            orig_keys="image",
            meta_keys="pred_meta_dict",
            orig_meta_keys="image_meta_dict",
            meta_key_postfix="meta_dict",
            nearest_interp=True,
            to_tensor=True,
        ),
        AsDiscreted(keys="pred", argmax=True),
        SaveImaged(
            keys="pred",
            meta_keys="pred_meta_dict",
            output_postfix="pred",
            output_dir=output_path,
            separate_folder=False,
            resample=False,
            output_dtype=np.uint8,  # Save as uint8 for segmentation masks
            savepath_in_metadict=True,
        )
    ])

    # Prepare input
    test_data = [{"image": input_image_path}]
    test_ds = Dataset(data=test_data, transform=inference_transform)
    test_loader = DataLoader(test_ds, batch_size=1, num_workers=0)

    # Inference loop
    with torch.no_grad():
        for batch in test_loader:
            test_inputs = batch["image"].to(device)
            test_outputs = sliding_window_inference(
                test_inputs, (160, 160, 160), 4, model
            )

            # Prepare for inversion and saving
            decollated_outputs = decollate_batch(test_outputs)
            batch_data = []
            for i, pred in enumerate(decollated_outputs):
                batch_data.append({
                    "pred": pred,
                    "image": batch["image"][i],
                    "pred_meta_dict": batch["image_meta_dict"],
                    "image_meta_dict": batch["image_meta_dict"]
                })

            # Apply post transforms (inversion + save)
            for data_dict in batch_data:
                post_transforms(data_dict)

            # Strip extension and add _pred.nii.gz
            base_name = os.path.basename(input_image_path)
            if base_name.endswith(".nii.gz"):
                pred_name = base_name.replace(".nii.gz", "_pred.nii.gz")
            else:
                pred_name = os.path.splitext(base_name)[0] + "_pred.nii.gz"

            pred_path = os.path.join(output_path, pred_name)

            visualize_preprocessed_image(
                nib.load(input_image_path).get_fdata(),
                nib.load(pred_path).get_fdata().astype(np.uint8),
                valohai.outputs("my-output").path("sample_inference.png")
            )

    print(f"Segmentation mask saved to: {output_path}")


if __name__ == "__main__":
    model_ckpt = valohai.inputs('model').path(process_archives=False)
    input = valohai.inputs('image').path(process_archives=False)
    output = valohai.outputs("my-output").path('predictions')

    args = parse_args()

    # initialize model
    model = get_model_network(
        in_channels=args.in_channels,
        out_channels=args.out_channels,
        num_res_units=args.num_res_units,
        channels=args.channels
    )

    run_inference(model_ckpt, input, output, model)
