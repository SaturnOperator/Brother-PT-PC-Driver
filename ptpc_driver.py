import serial
import time
import math
from PIL import Image
import sys

PORT_NAME = '/dev/cu.usbserial-FTEFZBD9'

# --- Configuration ---
COM_PORT = PORT_NAME
BAUD_RATE = 9600
BAND_HEIGHT = 24       # Printer fixed band height
# The printable area for 24mm tape is effectively 120 dots (5 bands)
SAFE_PRINT_HEIGHT = 120 

IMG_PATH = sys.argv[1]

def safe_send(ser, data, chunk_size=32):
    """Spoon-feed data to respect the printer's small buffer"""
    total_len = len(data)
    for i in range(0, total_len, chunk_size):
        chunk = data[i:i+chunk_size]
        ser.write(chunk)
        ser.flush() 
        time.sleep(0.02) 

def check_status(ser, step_name):
    ser.reset_input_buffer()
    ser.write(b'\x1B\x69\x53')
    time.sleep(0.2)
    response = ser.read(32)

    if len(response) != 32:
        return None

    # Simple error check
    if response[8] != 0 or response[9] != 0:
        print(f"Status Error [{step_name}]: Byte8={response[8]}, Byte9={response[9]}")
    
    return response

def wait_for_printer_ready(ser, step_name="Waiting"):
    for _ in range(5):
        response = check_status(ser, step_name)
        if response is not None:
            if response[8] == 0 and response[9] == 0:
                return True
        time.sleep(0.5)
    return False

def load_bitmap_to_canvas(path):
    # Load image and convert to 1-bit monochrome
    img = Image.open(path).convert("1")
    
    width, height = img.size
    
    # Force height to 120 dots to fit the tape bands.
    if height != SAFE_PRINT_HEIGHT:
        aspect_ratio = width / height
        new_width = int(SAFE_PRINT_HEIGHT * aspect_ratio)
        print(f"Resizing: {width}x{height} -> {new_width}x{SAFE_PRINT_HEIGHT}")
        img = img.resize((new_width, SAFE_PRINT_HEIGHT))
        width, height = img.size

    pixels = img.load()
    # Convert PIL pixels (0=Black, 255=White) to Printer bits (1=Black, 0=White)
    canvas = [[0 for _ in range(width)] for _ in range(height)]
    for y in range(height):
        for x in range(width):
            canvas[y][x] = 0 if pixels[x, y] else 1
    return canvas

def generate_full_bands(canvas):
    width = len(canvas[0])
    height = len(canvas)
    # Strictly 5 bands (120 dots)
    num_bands = 5 
    bands = []

    for band_idx in range(num_bands):
        start_row = band_idx * BAND_HEIGHT
        band_data = bytearray()
        for x in range(width):
            col_bits = 0
            # Pack 24 vertical pixels into 3 bytes
            for bit_offset in range(BAND_HEIGHT):
                y = start_row + bit_offset
                if y < height and canvas[y][x]:
                    col_bits |= (1 << (23 - bit_offset))
            
            band_data.extend([
                (col_bits >> 16) & 0xFF,
                (col_bits >> 8) & 0xFF,
                col_bits & 0xFF
            ])
        bands.append((band_data, width))
    return bands

def main():
    try:
        ser = serial.Serial(
            COM_PORT, 
            BAUD_RATE, 
            timeout=5, 
            dsrdtr=True,
            xonxoff=True
        )
        
        print(f"Connected to {COM_PORT}")
        
        # 1. Initialize
        ser.write(b'\x1B\x40')
        time.sleep(0.5)
        
        if not wait_for_printer_ready(ser, "Init"):
            print("Printer not ready.")
            return

        # 2. Set Mode (Enable Auto-Cut)
        # Bit 6=1 (Auto Cut On), Feed=Large
        ser.write(b'\x1B\x69\x4D\x40') 

        # ser.write(b'\x1B\x69\x4D\x00') # Auto cut off
        time.sleep(0.1)

        # 3. Process Image
        canvas = load_bitmap_to_canvas(IMG_PATH)
        bands = generate_full_bands(canvas)
        
        print(f"Printing Label: {len(canvas[0])} dots wide.")

        # 4. Send Bands (Lines)
        for i, (band_data, width) in enumerate(bands):
            print(f"Sending Band {i+1}/5...")
            
            n1 = width & 0xFF
            n2 = (width >> 8) & 0xFF
            
            # ESC * m(39) n1 n2 [DATA]
            ser.write(bytearray([0x1B, 0x2A, 39, n1, n2]))
            safe_send(ser, band_data)
            
            # CR LF (Move cursor to start of next band)
            ser.write(b'\x0D\x0A')    
            time.sleep(0.1)

        print("Triggering Print & Cut...")
        # [FIX 3] CTRL-Z (1A) to Print AND Cut
        ser.write(b'\x0C') # Don't cut
        
        # Wait for mechanical action
        time.sleep(4)
        ser.close()
        print("Done.")

    except Exception as e:
        print(f"CRASH: {e}")

if __name__ == "__main__":
    main()
