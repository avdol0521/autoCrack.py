#!/usr/bin/env python3
"""
auto_crack.py

Usage:
    auto_crack.py <hash_file> <wordlist>

What it does:
• Maintains per-<hash,mode_id> tracking in crack_log.json to avoid redundant cracking.
• Reads all valid hashes from <hash_file> (ignores empty lines and comments).
• Uses `hashcat --show <hash_file>` to enumerate structurally compatible hash modes.
• For each candidate mode:
    – Filters out hashes already attempted with this mode (via log).
    – Writes remaining hashes to a temporary subset file.
    – Creates a mode-specific temp potfile and runs Hashcat with:
        hashcat -m <mode> -a 0 --quiet --potfile-path <pot> <subset_file> <wordlist>
    – If exit code == 0, reruns Hashcat with `--show` to get cracked results.
        • Prints “[FOUND]” and the <hash> → '<clear>' for each.
        • Logs each cracked result with mode, cleartext, and UTC timestamp.
    – If nothing was cracked, prints “[NOT FOUND]”.
    – Cleans up temporary potfile and subset file after each mode.
• At the end, prints a summary of all newly cracked hashes per mode.

Example:
    ./auto_crack.py hashes.txt rockyou.txt
"""

import subprocess
import sys
import os
import re
import tempfile
import json
from datetime import datetime, timezone

LOG_FILE = "crack_log.json"

def validate_args():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <hash_file> <wordlist>")
        sys.exit(1)

    hash_file = sys.argv[1]
    wordlist  = sys.argv[2]

    if not os.path.isfile(hash_file) or os.path.getsize(hash_file) == 0:
        print(f"Error: Hash file '{hash_file}' does not exist or is empty.")
        sys.exit(1)

    if not os.path.isfile(wordlist) or os.path.getsize(wordlist) == 0:
        print(f"Error: Wordlist '{wordlist}' does not exist or is empty.")
        sys.exit(1)

    return hash_file, wordlist

def load_log_entries():
    """
    Load existing log entries from crack_log.json, each entry is
    { "hash": ..., "mode_id": ..., "clear": ..., "timestamp": ... }.
    Returns a set of (hash, mode_id) tuples, and the full list for appending.
    """
    if os.path.isfile(LOG_FILE):
        try:
            with open(LOG_FILE, "r") as f:
                data = json.load(f)
                seen = {(entry["hash"], entry["mode_id"]) for entry in data}
                return seen, data
        except Exception:
            pass
    return set(), []

def append_log(entries_list, entry):
    """
    Append a single entry dict to entries_list and write out to LOG_FILE.
    """
    entries_list.append(entry)
    with open(LOG_FILE, "w") as f:
        json.dump(entries_list, f, indent=2)

def get_all_hashes(hash_file):
    """
    Read all non-empty, non-comment lines from hash_file.
    """
    hashes = []
    with open(hash_file, "r") as f:
        for line in f:
            h = line.strip()
            if h and not h.startswith("#"):
                hashes.append(h)
    return hashes

def get_candidate_modes(hash_file):
    """
    Runs: hashcat --show <hash_file>
    Parses its stdout to extract lines like:
        1400 | SHA2-256   | Raw Hash
        17400 | SHA3-256  | Raw Hash
        …
    Returns a list of (mode_id, mode_name) tuples.
    """
    try:
        proc = subprocess.run(
            ["hashcat", "--show", hash_file],
            capture_output=True,
            text=True
        )
    except FileNotFoundError:
        print("Error: `hashcat` not found in PATH.")
        sys.exit(1)

    output = proc.stdout.splitlines()
    candidate_modes = []
    pattern = re.compile(r'^\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(.+)$')

    for line in output:
        match = pattern.match(line)
        if match:
            mode_id   = match.group(1).strip()
            mode_name = match.group(2).strip()
            candidate_modes.append((mode_id, mode_name))

    return candidate_modes

def run_hashcat_with_pot(mode, subset_file, wordlist, potpath):
    """
    Runs: hashcat -m <mode> -a 0 --quiet --potfile-path <potpath> <subset_file> <wordlist>
    Returns True if exit-code == 0 (i.e. cracked ≥1 hash), else False.
    """
    proc = subprocess.run(
        ["hashcat", "-m", mode, "-a", "0", "--quiet", "--potfile-path", potpath, subset_file, wordlist],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    return (proc.returncode == 0)

def extract_cracked_from_pot(mode, subset_file, wordlist, potpath):
    """
    After a successful run, call:
        hashcat -m <mode> -a 0 --quiet --potfile-path <potpath> --show <subset_file> <wordlist>
    Returns a list of (hash, cleartext) tuples that this mode cracked.
    """
    show_proc = subprocess.run(
        ["hashcat", "-m", mode, "-a", "0", "--quiet", "--potfile-path", potpath, "--show", subset_file, wordlist],
        capture_output=True,
        text=True
    )
    lines = show_proc.stdout.strip().splitlines()
    pairs = []
    for line in lines:
        if ":" in line:
            h, clear = line.split(":", 1)
            pairs.append((h, clear))
    return pairs

def main():
    hash_file, wordlist = validate_args()

    # Load existing log
    already_cracked, log_entries = load_log_entries()

    print(f"→ Enumerating possible modes with `hashcat --show {hash_file}` …")
    candidates = get_candidate_modes(hash_file)

    if not candidates:
        print("No candidate modes found. Either your hash_file is invalid or Hashcat's output format changed.")
        sys.exit(1)

    # Read all hashes once
    all_hashes = get_all_hashes(hash_file)

    print(f"→ Found {len(candidates)} candidate mode(s):")
    for mode_id, mode_name in candidates:
        print(f"   • {mode_id}  ({mode_name})")

    cracked_summary = []  # list of (mode_id, mode_name, [(hash, clear), ...])

    print("\n→ Starting isolated crack attempts, one subset per mode …\n")

    for mode_id, mode_name in candidates:
        # Build subset of pending hashes for this mode
        pending = [h for h in all_hashes if (h, mode_id) not in already_cracked]
        if not pending:
            print(f"→ Mode {mode_id:<6} ({mode_name}) … [SKIPPED] no new hashes for this mode. delete crack_log.json if you wish to still run this mode ig??")
            continue

        print(f"→ Mode {mode_id:<6} ({mode_name}) … ", end="", flush=True)

        # Write pending hashes to a temporary subset file
        tf = tempfile.NamedTemporaryFile(prefix="hc_subset_", delete=False, mode="w")
        for h in pending:
            tf.write(h + "\n")
        tf.flush()
        tf.close()
        subset_file = tf.name

        # 1) Create a unique temp potfile for this mode
        tmp_pot = tempfile.NamedTemporaryFile(prefix="hc_pot_", delete=False)
        tmp_pot_path = tmp_pot.name
        tmp_pot.close()

        # 2) Run hashcat on the subset
        cracked = run_hashcat_with_pot(mode_id, subset_file, wordlist, tmp_pot_path)

        if cracked:
            # 3) Extract exactly what this mode cracked
            pairs = extract_cracked_from_pot(mode_id, subset_file, wordlist, tmp_pot_path)
            print("[FOUND]")
            for h, clear in pairs:
                print(f"    {h} → '{clear}'")
                # Log each newly cracked <hash, mode_id, clear, timestamp>
                entry = {
                    "hash": h,
                    "mode_id": mode_id,
                    "clear": clear,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                append_log(log_entries, entry)
                cracked_summary.append((mode_id, mode_name, pairs))
        else:
            print("[NOT FOUND]")

        # 4) Cleanup
        try:
            os.remove(tmp_pot_path)
        except OSError:
            pass
        try:
            os.remove(subset_file)
        except OSError:
            pass

    # Final summary
    print("\n=== Summary of All Modes That Actually Cracked Something ===")
    if not cracked_summary:
        print("No modes cracked any new hashes. Check your wordlist and/or hash file.")
        sys.exit(0)

    for mode_id, mode_name, pairs in cracked_summary:
        # Note: pairs may repeat across modes, but we only log newly cracked ones above.
        print(f"\n→ Mode {mode_id} ({mode_name}) cracked {len(pairs)} hash(es):")
        for h, clear in pairs:
            print(f"    {h} → '{clear}'")

    print("\nAll done :D")
    sys.exit(0)

if __name__ == "__main__":
    main()
# .     .       .  .   . .   .   . .    +  .
#   .     .  :     .    .. :. .___---------___.
#        .  .   .    .  :.:. _".^ .^ ^.  '.. :"-_. .
#     .  :       .  .  .:../:            . .^  :.:\.
#         .   . :: +. :.:/: .   .    .        . . .:\
#  .  :    .     . _ :::/:               .  ^ .  . .:\
#   .. . .   . - : :.:./.                        .  .:\
#   .      .     . :..|:                    .  .  ^. .:|
#     .       . : : ..||        .                . . !:|
#   .     . . . ::. ::\(                           . :)/
#  .   .     : . : .:.|. ######              .#######::|
#   :.. .  :-  : .:  ::|.#######           ..########:|
#  .  .  .  ..  .  .. :\ ########          :######## :/
#   .        .+ :: : -.:\ ########       . ########.:/
#     .  .+   . . . . :.:\. #######       #######..:/
#       :: . . . . ::.:..:.\           .   .   ..:/
#    .   .   .  .. :  -::::.\.       | |     . .:/
#       .  :  .  .  .-:.":.::.\             ..:/
#  .      -.   . . . .: .:::.:.\.           .:/
# .   .   .  :      : ....::_:..:\   ___.  :/
#    .   .  .   .:. .. .  .: :.:.:\       :/
#      +   .   .   : . ::. :.:. .:.|\  .:/|
#      .         +   .  .  ...:: ..|  --.:|
# .      . . .   .  .  . ... :..:.."(  ..)"
#  .   .       .      :  .   .: ::/  .  .::\ 