import pandas as pd
import lxml.etree as ET
import os

# Configuration
METADATA_FILE = 'spokensongs_metadata_test.csv'
MEI_DIR = './mei_from_musescore' # Directory where your MuseScore MEI files are
OUTPUT_DIR = './mei_with_metadata'

if not os.path.exists(OUTPUT_DIR): os.makedirs(OUTPUT_DIR)

def update_mei_metadata():
    df = pd.read_csv(METADATA_FILE)
    ns = {'mei': 'http://www.music-encoding.org/ns/mei'}
    
    for _, row in df.iterrows():
        mei_filename = f"{row['musicxml_filename']}.mei"
        input_path = os.path.join(MEI_DIR, mei_filename)
        
        if not os.path.exists(input_path):
            print(f"File not found: {input_path}, skipping...")
            continue
            
        print(f"Updating metadata for: {mei_filename}")
        
        # Parse the MEI file
        parser = ET.XMLParser(remove_blank_text=True)
        tree = ET.parse(input_path, parser)
        root = tree.getroot()
        
        # Locate or create <meiHead>
        meiHead = root.find('.//mei:meiHead', ns)
        if meiHead is None:
            meiHead = ET.SubElement(root, '{http://www.music-encoding.org/ns/mei}meiHead')
        
        # 1. Update <fileDesc> (Columns A-C)
        fileDesc = meiHead.find('.//mei:fileDesc', ns)
        if fileDesc is None:
            fileDesc = ET.SubElement(meiHead, '{http://www.music-encoding.org/ns/mei}fileDesc')
        
        titleStmt = fileDesc.find('.//mei:titleStmt', ns)
        if titleStmt is None:
            titleStmt = ET.SubElement(fileDesc, '{http://www.music-encoding.org/ns/mei}titleStmt')
        
        # Update or create Title
        title = titleStmt.find('.//mei:title', ns)
        if title is None:
            title = ET.SubElement(titleStmt, '{http://www.music-encoding.org/ns/mei}title')
        title.text = str(row['workTitle'])
        
        # 2. Add remaining columns to <otherMeta>
        # Clear existing otherMeta if you are updating files iteratively
        old_meta = meiHead.find('.//mei:otherMeta', ns)
        if old_meta is not None:
            meiHead.remove(old_meta)
            
        otherMeta = ET.SubElement(meiHead, '{http://www.music-encoding.org/ns/mei}otherMeta')
        
        for col in df.columns:
            if col in ['musicxml_filename', 'workId', 'workTitle']: continue
            if pd.notna(row[col]):
                ET.SubElement(otherMeta, '{http://www.music-encoding.org/ns/mei}meta', 
                              name=col, content=str(row[col]))
        
        # Save
        tree.write(os.path.join(OUTPUT_DIR, mei_filename), 
                   encoding='utf-8', xml_declaration=True, pretty_print=True)
        print(f"  Saved metadata to: {OUTPUT_DIR}/{mei_filename}")

if __name__ == "__main__":
    update_mei_metadata()