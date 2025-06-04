# autoCrack.py

A Python wrapper for automating Hashcat across multiple hash modes with per-hash-mode tracking and zero overlap. got lazy. didn't wanna check 8 hash algos wish hashcat manually. vibe coded a simple script to automate hash cracking. still some stuff left to improve tho. have fun :D

## Features:

- Automatically detects candidate hash modes using `hashcat --show`.
    
- Cracks each mode in isolation using temporary potfiles.
    
- Tracks which `<hash,mode>` pairs have already been tested using `crack_log.json`.
    
- Efficient: skips hashes already cracked or previously attempted with that mode or if theres a saved log for that hash for that specific mode in `crack_log.json`.
    
- Clean, readable output and full summary of cracked hashes.
    
- Self-cleaning: deletes all temporary files after use.
    

---

## Requirements:

- Python 3.6+
    
- Hashcat installed and accessible in your system's `$PATH`.
    

---

## Usage:

`./auto_crack.py <hash_file> <wordlist>`

### Example:

`./auto_crack.py hashes.txt rockyou.txt`

---

## How It Works:

1. Validates the hash file and wordlist.
    
2. Uses `hashcat --show <hash_file>` to identify candidate hash modes.
    
3. For each matching mode:
    
    - Skips hashes already cracked or attempted (via `crack_log.json`).
        
    - Writes remaining hashes to a temporary subset file.
        
    - Runs Hashcat with a unique temporary potfile.
        
    - If any hashes are cracked:
        
        - Displays each `<hash> → '<plaintext>'`.
            
        - Logs mode, hash, plaintext, and timestamp to `crack_log.json`.
            
4. Cleans up all temporary files.
    
5. Displays a summary of all successfully cracked hashes.

## Stuff ill fix/add later when im not feeling lazy:

  - fix potfile carryover when multiple hashes in same file

  - add a progress bar and stuff maybe to make it less silent
