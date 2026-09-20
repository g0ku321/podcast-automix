#!/usr/bin/env python3
"""
Podcast Deep Dugan Automixer
============================
A fast, memory-safe multi-mic automixer for 2-speaker podcast recordings (e.g. Zoom PodTrak, Rodecaster).
Eliminates cross-mic acoustic bleed while keeping 100% of the natural vocal sound with zero sudden drops.
"""

import os
import sys
import glob
import time
import argparse
import subprocess
import soundfile as sf
import numpy as np
from scipy import signal

def parse_args():
    parser = argparse.ArgumentParser(
        description="Fast, broadcast-quality Dugan Speech Automixer for 2-microphone podcast recordings.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("input_dir", help="Directory containing the input multi-mic WAV files.")
    parser.add_argument("-o", "--output-dir", help="Directory to save automixed files (defaults to <input_dir> AUTOMIXED).")
    parser.add_argument("--mic1-pattern", default="MIC1*.WAV", help="Glob pattern for Host / Mic 1 files.")
    parser.add_argument("--mic2-pattern", default="MIC2*.WAV", help="Glob pattern for Guest / Mic 2 files.")
    parser.add_argument("--mic1-floor", type=float, default=-20.0, help="Attenuation floor in dB for Mic 1 when Speaker 2 talks.")
    parser.add_argument("--mic2-floor", type=float, default=-26.0, help="Attenuation floor in dB for Mic 2 when Speaker 1 talks.")
    parser.add_argument("--mic1-boost", type=float, default=7.0, help="Detector priority boost in dB for Mic 1 (compensates for high gain on Mic 2).")
    parser.add_argument("--chunk-size", type=int, default=60, help="Processing chunk size in seconds (keeps RAM usage at ~30 MB).")
    parser.add_argument("--skip-mp3", action="store_true", help="Skip generating 320 kbps MP3 files (output WAV only).")
    parser.add_argument("--skip-wav", action="store_true", help="Skip generating 24-bit 48kHz WAV files (output MP3 only).")
    return parser.parse_args()

def find_pairs(input_dir, p1_pattern, p2_pattern):
    # Case-insensitive search for files
    all_files = os.listdir(input_dir)
    m1_files = sorted([f for f in all_files if f.upper().startswith("MIC1") and f.upper().endswith(".WAV")])
    m2_files = sorted([f for f in all_files if f.upper().startswith("MIC2") and f.upper().endswith(".WAV")])
    
    pairs = []
    for f1 in m1_files:
        # Match suffix (e.g. "MIC1.WAV" -> suffix "", "MIC1 2.WAV" -> suffix " 2")
        stem = f1[:-4] # strip .WAV
        suffix = stem[4:] # strip "MIC1"
        target_stem = "MIC2" + suffix
        # Find matching f2
        match = None
        for f2 in m2_files:
            if f2[:-4].upper() == target_stem.upper():
                match = f2
                break
        if match:
            pairs.append((f1, match, suffix.strip() or "Take1"))
        else:
            print(f"Warning: Could not find matching Mic 2 file for '{f1}'. Skipping.")
    return pairs

def process_pair(p1_path, p2_path, take_name, out_dir, args):
    t_start = time.time()
    base1 = os.path.splitext(os.path.basename(p1_path))[0]
    base2 = os.path.splitext(os.path.basename(p2_path))[0]
    
    out1_base = f"{base1}_Automixed"
    out2_base = f"{base2}_Automixed"
    
    out1_wav = os.path.join(out_dir, out1_base + ".wav")
    out1_mp3 = os.path.join(out_dir, out1_base + ".mp3")
    out2_wav = os.path.join(out_dir, out2_base + ".wav")
    out2_mp3 = os.path.join(out_dir, out2_base + ".mp3")
    
    temp1_wav = os.path.join(out_dir, f"temp_{out1_base}.wav")
    temp2_wav = os.path.join(out_dir, f"temp_{out2_base}.wav")
    
    print(f"\n=======================================================")
    print(f"Processing: {os.path.basename(p1_path)} & {os.path.basename(p2_path)}")
    print(f"=======================================================")
    
    with sf.SoundFile(p1_path) as s1, sf.SoundFile(p2_path) as s2:
        sr = s1.samplerate
        total_frames = min(len(s1), len(s2))
        dur_mins = (total_frames / sr) / 60.0
        print(f"Total Duration: {dur_mins:.2f} minutes ({total_frames} samples at {sr} Hz)")
        
        w1 = sf.SoundFile(temp1_wav, mode='w', samplerate=sr, channels=1, subtype='PCM_24')
        w2 = sf.SoundFile(temp2_wav, mode='w', samplerate=sr, channels=1, subtype='PCM_24')
        
        gain_comp1 = 10 ** (args.mic1_boost / 20.0)
        att_alpha = np.exp(-1.0 / (sr * 0.012))
        dec_alpha = np.exp(-1.0 / (sr * 0.250))
        
        min_gain_1 = 10 ** (args.mic1_floor / 20.0)
        min_gain_2 = 10 ** (args.mic2_floor / 20.0)
        noise_floor = 10 ** (-44.0 / 20.0)
        
        smooth_samples = int(0.025 * sr)
        kernel = np.hanning(smooth_samples)
        kernel /= np.sum(kernel)
        
        curr1 = 0.0
        curr2 = 0.0
        
        chunk_frames = int(args.chunk_size * sr)
        frames_processed = 0
        last_progress_print = time.time()
        
        while frames_processed < total_frames:
            n_to_read = min(chunk_frames, total_frames - frames_processed)
            c1 = s1.read(n_to_read, dtype='float32')
            c2 = s2.read(n_to_read, dtype='float32')
            if len(c1) == 0:
                break
                
            cal1 = c1 * gain_comp1
            cal2 = c2
            
            abs_1 = np.abs(cal1)
            abs_2 = np.abs(cal2)
            env1 = np.empty_like(abs_1)
            env2 = np.empty_like(abs_2)
            
            for i in range(len(c1)):
                v1 = abs_1[i]
                v2 = abs_2[i]
                if v1 > curr1:
                    curr1 = v1 + att_alpha * (curr1 - v1)
                else:
                    curr1 = v1 + dec_alpha * (curr1 - v1)
                env1[i] = curr1
                
                if v2 > curr2:
                    curr2 = v2 + att_alpha * (curr2 - v2)
                else:
                    curr2 = v2 + dec_alpha * (curr2 - v2)
                env2[i] = curr2
                
            total_level = env1 + env2 + noise_floor
            raw_g1 = (env1 + 0.3 * noise_floor) / total_level
            raw_g2 = (env2 + 0.3 * noise_floor) / total_level
            
            curve_g1 = raw_g1 ** 1.6
            curve_g2 = raw_g2 ** 1.6
            norm_sum = curve_g1 + curve_g2 + 1e-6
            curve_g1 /= norm_sum
            curve_g2 /= norm_sum
            
            g1 = min_gain_1 + (1.0 - min_gain_1) * curve_g1
            g2 = min_gain_2 + (1.0 - min_gain_2) * curve_g2
            
            g1_smooth = signal.convolve(g1, kernel, mode='same')
            g2_smooth = signal.convolve(g2, kernel, mode='same')
            g1_smooth = np.clip(g1_smooth, min_gain_1, 1.0)
            g2_smooth = np.clip(g2_smooth, min_gain_2, 1.0)
            
            w1.write(c1 * g1_smooth)
            w2.write(c2 * g2_smooth)
            
            frames_processed += len(c1)
            if time.time() - last_progress_print >= 5.0:
                pct = (frames_processed / total_frames) * 100
                print(f"  Processed {frames_processed / sr / 60:.1f} / {dur_mins:.1f} mins ({pct:.1f}%)...")
                last_progress_print = time.time()
                
        w1.close()
        w2.close()
        
    # Export final formats
    if not args.skip_wav:
        print(f"Exporting broadcast 48 kHz 24-bit WAVs...")
        subprocess.run(["ffmpeg", "-y", "-nostdin", "-i", temp1_wav, "-ar", "48000", "-c:a", "pcm_s24le", out1_wav], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["ffmpeg", "-y", "-nostdin", "-i", temp2_wav, "-ar", "48000", "-c:a", "pcm_s24le", out2_wav], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
    if not args.skip_mp3:
        print(f"Encoding 320 kbps MP3s...")
        src_for_mp3_1 = out1_wav if not args.skip_wav else temp1_wav
        src_for_mp3_2 = out2_wav if not args.skip_wav else temp2_wav
        subprocess.run(["ffmpeg", "-y", "-nostdin", "-i", src_for_mp3_1, "-c:a", "libmp3lame", "-b:a", "320k", out1_mp3], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["ffmpeg", "-y", "-nostdin", "-i", src_for_mp3_2, "-c:a", "libmp3lame", "-b:a", "320k", out2_mp3], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
    if os.path.exists(temp1_wav):
        os.remove(temp1_wav)
    if os.path.exists(temp2_wav):
        os.remove(temp2_wav)
        
    elapsed = time.time() - t_start
    print(f"SUCCESS: Completed in {elapsed:.1f}s ({elapsed/60:.2f} mins)!")

def main():
    args = parse_args()
    input_dir = os.path.abspath(args.input_dir)
    if not os.path.isdir(input_dir):
        print(f"Error: Input directory '{input_dir}' does not exist.")
        sys.exit(1)
        
    if args.output_dir:
        output_dir = os.path.abspath(args.output_dir)
    else:
        # Default: <input_dir> AUTOMIXED
        parent_dir = os.path.dirname(input_dir)
        folder_name = os.path.basename(input_dir)
        output_dir = os.path.join(parent_dir, f"{folder_name} AUTOMIXED")
        
    os.makedirs(output_dir, exist_ok=True)
    print(f"\n🎙️  Podcast Deep Dugan Automixer")
    print(f"Input Directory:  {input_dir}")
    print(f"Output Directory: {output_dir}")
    print(f"Parameters:       Mic 1 Floor: {args.mic1_floor} dB | Mic 2 Floor: {args.mic2_floor} dB | Mic 1 Priority Boost: +{args.mic1_boost} dB")
    
    pairs = find_pairs(input_dir, args.mic1_pattern, args.mic2_pattern)
    if not pairs:
        print("No matching microphone pairs found in input directory.")
        sys.exit(1)
        
    print(f"Found {len(pairs)} microphone pair(s) to process:")
    for p1, p2, suffix in pairs:
        print(f"  • {p1} <-> {p2}")
        
    total_start = time.time()
    for p1, p2, suffix in pairs:
        p1_path = os.path.join(input_dir, p1)
        p2_path = os.path.join(input_dir, p2)
        process_pair(p1_path, p2_path, suffix, output_dir, args)
        
    total_elapsed = time.time() - total_start
    print(f"\n🎉 All {len(pairs)} takes finished in {total_elapsed/60:.2f} minutes!")
    print(f"📁 Output files saved to: {output_dir}\n")

if __name__ == "__main__":
    main()
