import serial
import time
import math
from PIL import Image

PORT_NAME = '/dev/cu.usbserial-FTEFZBD9' #Replace with serial port name

# --- Configuration ---
COM_PORT = PORT_NAME
BAUD_RATE = 9600
BAND_HEIGHT = 24
BITMAP_PATH = "bitmap.bmp"

# Safe printable height for 24mm tape using 24-dot bands is 120 dots (5 bands)
# (6 bands = 144 dots, which overflows the 128-dot limit)
SAFE_PRINT_HEIGHT = 120 

def safe_send(ser, data, chunk_size=32):
    """Helps respects the printer's 512B reception buffer"""
    total_len = len(data)
    for i in range(0, total_len, chunk_size):
        chunk = data[i:i+chunk_size]
        ser.write(chunk)
        ser.flush() 
        time.sleep(0.02) 

def parse_status_byte(byte_val, mapping):
    return [desc for bit, desc in mapping.items() if byte_val & (1 << bit)]

def check_status(ser, step_name):
    ser.reset_input_buffer()
    ser.write(b'\x1B\x69\x53')
    time.sleep(0.2)
    response = ser.read(32)

    print(f"\n--- Status Check: {step_name} ---")
    if len(response) != 32:
        print(f"Incomplete status packet ({len(response)} bytes)")
        return None

    err1_map = {0: "NO TAPE", 1: "TAPE END", 2: "CUTTER JAM"}
    err2_map = {0: "TAPE CHANGE ERR", 1: "PRINT BUFFER FULL",
                2: "TRANSMISSION ERR", 3: "RX BUFFER FULL"}

    errors = parse_status_byte(response[8], err1_map) + parse_status_byte(response[9], err2_map)
    if errors:
        print(f"CRITICAL ERRORS: {errors}")
    else:
        print("Errors: None")
    
    print(f"Tape Width: {response[10]} mm")
    return response

def wait_for_printer_ready(ser, step_name="Waiting"):
    for _ in range(5):
        response = check_status(ser, step_name)
        if response is not None:
            if response[8] == 0 and response[9] == 0:
                return True
            # If buffer full, we can't recover without a reset
            if (response[9] & 2): 
                print("Buffer Full Error detected during wait.")
                return False
        time.sleep(0.5)
    return False

def load_bitmap_to_canvas(path):
    img = Image.open(path).convert("1")
    width, height = img.size
    
    # Resize to the SAFE height (120 dots) to prevent overflow
    if height != SAFE_PRINT_HEIGHT:
        print(f"Resizing image height from {height} to {SAFE_PRINT_HEIGHT} (5 bands)...")
        img = img.resize((width, SAFE_PRINT_HEIGHT))
        width, height = img.size

    pixels = img.load()
    canvas = [[0 for _ in range(width)] for _ in range(height)]
    for y in range(height):
        for x in range(width):
            canvas[y][x] = 0 if pixels[x, y] else 1
    return canvas

def generate_full_bands(canvas):
    width = len(canvas[0])
    height = len(canvas)
    # Force strictly 5 bands for safety on 24mm tape
    num_bands = 5 
    bands = []

    print(f"Generating {num_bands} bands from canvas...")

    for band_idx in range(num_bands):
        start_row = band_idx * BAND_HEIGHT
        band_data = bytearray()
        for x in range(width):
            col_bits = 0
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
        
        print(f"Opened {COM_PORT}")
        
        # 1. Initialize (Clear Buffer)
        ser.write(b'\x1B\x40')
        time.sleep(0.5)
        
        # 2. Check Status
        if not wait_for_printer_ready(ser, "After Init"):
            print("Printer error. Aborting.")
            return

        # 3. Set Mode (Auto Cut OFF)
        ser.write(b'\x1B\x69\x4D\x00') 
        time.sleep(0.1)

        # 4. Prepare Data
        canvas = load_bitmap_to_canvas(BITMAP_PATH)
        bands = generate_full_bands(canvas)
        
        print(f"\nPrinting: {len(canvas[0])} dots long. {len(bands)} bands.")

        # 5. Send Bands
        for i, (band_data, width) in enumerate(bands):
            print(f"Sending Band {i+1}/{len(bands)}...")
            
            n1 = width & 0xFF
            n2 = (width >> 8) & 0xFF
            
            # Send Command + Data
            ser.write(bytearray([0x1B, 0x2A, 39, n1, n2]))
            safe_send(ser, band_data)
            
            # Send CR LF to move to next band position
            ser.write(b'\x0D\x0A')    
            time.sleep(0.1)

        print("Triggering Print...")
        ser.write(b'\x0C') # FF (Print without Cut)
        # ser.write(b'\x1A') # (Print and Cut)
        
        time.sleep(2)
        wait_for_printer_ready(ser, "Final Check")
        ser.close()
        print("Done.")

    except Exception as e:
        print(f"CRASH: {e}")

if __name__ == "__main__":
    main()
