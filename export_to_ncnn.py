import os
from ultralytics import YOLO

MODELS_DIR = './models'

def export_all_models_to_ncnn():
    """
    Finds all .pt models in the MODELS_DIR and exports them to ncnn format.
    The exported models will be generated seamlessly as directories with '_ncnn_model' suffix.
    """
    print(f"Checking for .pt files in {MODELS_DIR} to export to NCNN format...")
    if not os.path.exists(MODELS_DIR):
        print(f"Directory {MODELS_DIR} not found.")
        return

    pt_files = [f for f in os.listdir(MODELS_DIR) if f.endswith('.pt')]
    
    if not pt_files:
        print(f"No .pt files found in {MODELS_DIR}.")
        return

    for pt_file in pt_files:
        full_path = os.path.join(MODELS_DIR, pt_file)
        ncnn_dir = full_path.replace('.pt', '_ncnn_model')
        
        if os.path.exists(ncnn_dir):
            print(f"NCNN model already exists for {pt_file} ({ncnn_dir}). Skipping.")
            continue
            
        print(f"\n--- Exporting {pt_file} to NCNN ---")
        try:
            model = YOLO(full_path)
            # imgsz=640 is standard default. Removing half=True to prevent FP16 nan zeroing bug on ARM CPUs.
            model.export(format="ncnn", imgsz=640)
            print(f"Successfully exported {pt_file} to {ncnn_dir}")
        except Exception as e:
            print(f"Error exporting {pt_file}: {e}")

if __name__ == "__main__":
    export_all_models_to_ncnn()
    print("\nAll export tasks finished. You can now run your API and it will prioritize NCNN models.")
