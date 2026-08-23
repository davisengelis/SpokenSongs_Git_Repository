#!/bin/bash

input_folder="$1"
output_folder="$2"

# Default values if not provided
input_folder="${input_folder:-.}"
output_folder="${output_folder:-metadati_collected}"

mkdir -p "$output_folder"

find "$input_folder" -type f \( -iname "*metadati*.png" -o -iname "*metadati*.jpg" -o -iname "*metadati*.jpeg" \) -exec cp {} "$output_folder" \;

echo "Copied metadata images from '$input_folder' to '$output_folder'."