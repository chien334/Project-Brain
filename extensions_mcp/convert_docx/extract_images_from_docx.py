from docx import Document
from docx.oxml import parse_xml
import os
from PIL import Image
import io

# Try to import pytesseract for OCR
try:
    import pytesseract
    HAS_TESSERACT = True
except ImportError:
    HAS_TESSERACT = False
    print("Warning: pytesseract not installed. Install with: pip install pytesseract")

doc = Document('docs-srs/Desola_Bug No 4_solution confirm_20260605.docx')

# Create output directory for images
output_dir = 'docs-srs/extracted_images'
os.makedirs(output_dir, exist_ok=True)

# Extract images from document
image_data = []
image_count = 0

for rel in doc.part.rels.values():
    if "image" in rel.target_ref:
        image_count += 1
        try:
            # Get image data
            image_part = rel.target_part
            image_bytes = image_part.blob
            
            # Save image file
            image_filename = f"{output_dir}/image_{image_count}.png"
            with open(image_filename, 'wb') as f:
                f.write(image_bytes)
            
            print(f"Extracted image {image_count}: {image_filename}")
            
            # Try to extract text from image using OCR
            if HAS_TESSERACT:
                try:
                    img = Image.open(io.BytesIO(image_bytes))
                    text = pytesseract.image_to_string(img)
                    image_data.append({
                        'image_num': image_count,
                        'filename': image_filename,
                        'text': text
                    })
                    print(f"OCR Text from image {image_count}:\n{text}\n")
                except Exception as e:
                    print(f"Error extracting text from image {image_count}: {e}")
                    image_data.append({
                        'image_num': image_count,
                        'filename': image_filename,
                        'text': f"[Error extracting text: {str(e)}]"
                    })
            else:
                image_data.append({
                    'image_num': image_count,
                    'filename': image_filename,
                    'text': "[pytesseract not available - install for OCR]"
                })
        except Exception as e:
            print(f"Error processing image {image_count}: {e}")

print(f"\nTotal images extracted: {image_count}")

# Write results to markdown
with open('docs-srs/Desola_Bug_No4_Images_Summary.md', 'w', encoding='utf-8') as f:
    f.write("# Desola Bug No 4 - Extracted Images and Text\n\n")
    f.write(f"## Total Images Found: {image_count}\n\n")
    
    for img_data in image_data:
        f.write(f"### Image {img_data['image_num']}\n")
        f.write(f"**File**: {img_data['filename']}\n\n")
        f.write(f"**Extracted Text**:\n```\n{img_data['text']}\n```\n\n")

print("Results saved to: docs-srs/Desola_Bug_No4_Images_Summary.md")
