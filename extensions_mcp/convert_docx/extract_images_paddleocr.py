from docx import Document
import os
from PIL import Image
import io

# Try to import paddleocr for OCR
try:
    from paddleocr import PaddleOCR
    HAS_PADDLEOCR = True
except ImportError:
    HAS_PADDLEOCR = False
    print("Installing paddleocr...")
    os.system('pip install paddleocr')
    try:
        from paddleocr import PaddleOCR
        HAS_PADDLEOCR = True
    except:
        HAS_PADDLEOCR = False

doc = Document('docs-srs/Desola_Bug No 4_solution confirm_20260605.docx')

# Create output directory for images
output_dir = 'docs-srs/extracted_images'
os.makedirs(output_dir, exist_ok=True)

# Extract images from document
image_data = []
image_count = 0

# Initialize OCR reader if available
ocr = None
if HAS_PADDLEOCR:
    try:
        print("Initializing PaddleOCR reader...")
        ocr = PaddleOCR(use_angle_cls=True, lang='en')
        print("PaddleOCR reader initialized successfully")
    except Exception as e:
        print(f"Error initializing PaddleOCR reader: {e}")
        HAS_PADDLEOCR = False

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
            if HAS_PADDLEOCR and ocr:
                try:
                    # Run OCR using predict method
                    results = ocr.predict(image_filename)
                    
                    # Extract text from results
                    text_lines = []
                    if results and len(results) > 0:
                        for line in results[0]:
                            if line and len(line) > 1:
                                text_lines.append(line[1][0])
                    
                    text = '\n'.join(text_lines)
                    
                    image_data.append({
                        'image_num': image_count,
                        'filename': image_filename,
                        'text': text if text.strip() else "[No text detected in image]"
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
                    'text': "[OCR not available]"
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
