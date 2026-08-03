#!/usr/bin/env python3
"""
Restore GPG signatures from commit messages.

This script processes a git repository where GPG signatures have been stored
in commit messages by store_signatures.py (with the format
"original_gpgsig <type> <original-message-length>\n<data>") and restores them
as proper git commit signatures.

The original message length recorded in the trailer is used to slice the
message back out exactly, byte for byte, instead of inferring the boundary
from a fixed number of newlines (which breaks when the original message
itself ends in a blank line) or trusting the first text match of the marker
(which could coincidentally appear in a real commit message).

Usage:
    restore_signatures.py [--refs <refs>...]

If no refs are specified, processes all refs.
"""

import sys
import subprocess
import argparse


def extract_signature_from_message(commit_msg):
    """
    Extract GPG signature from commit message if present.

    Returns:
        tuple: (cleaned_message, sig_type, sig_data) or (commit_msg, None, None)
    """
    sig_identifier = b"\noriginal_gpgsig "
    # Our own trailer is always appended last, so it's the last occurrence of
    # this marker in the message. Searching from the end makes this immune to
    # a real commit message that happens to contain similar text earlier in
    # its body.
    sig_index = commit_msg.rfind(sig_identifier)

    if sig_index == -1:
        return commit_msg, None, None

    sig_section = commit_msg[sig_index + len(sig_identifier):]

    first_newline = sig_section.find(b'\n')
    if first_newline == -1:
        # Malformed signature
        return commit_msg, None, None

    header = sig_section[:first_newline]
    # sig_type itself can contain a space (e.g. "sha1 openpgp"), so only
    # split off the trailing length token.
    header_parts = header.rsplit(b' ', 1)
    if len(header_parts) != 2 or not header_parts[1].isdigit():
        return commit_msg, None, None

    sig_type, orig_len = header_parts[0], int(header_parts[1])

    # Sanity check: for a genuine trailer, sig_index is by construction equal
    # to the original message length. A mismatch means this wasn't actually
    # our trailer (e.g. a coincidental match) - leave the message untouched.
    if orig_len != sig_index:
        return commit_msg, None, None

    cleaned_msg = commit_msg[:orig_len]
    sig_data = sig_section[first_newline + 1:]

    return cleaned_msg, sig_type, sig_data


def process_fast_export_stream(input_stream, output_stream):
    """
    Process git fast-export stream, restoring signatures from messages.
    """
    commits_processed = 0
    signatures_restored = 0

    for line in input_stream:
        # Pass through non-commit lines
        if not line.startswith(b'commit '):
            output_stream.write(line)
            continue

        commits_processed += 1

        # Write commit line
        output_stream.write(line)

        # Collect commit headers
        headers = []
        commit_msg = None

        while True:
            line = input_stream.readline()

            if line.startswith(b'gpgsig '):
                # A real, un-stored signature is still attached to this
                # commit. Restoring on top of it would misparse the
                # signature's own 'data' block as the commit message and
                # silently corrupt the commit, so fail loudly instead.
                raise ValueError(
                    "Unexpected gpgsig header in restore input; the input "
                    "stream still has signatures attached. Was the store "
                    "step applied to this repository (and to the same "
                    "--refs)?"
                )

            if line.startswith(b'data '):
                # This is the commit message
                size = int(line.split()[1])
                commit_msg = input_stream.read(size)

                # Check for trailing newline
                next_line = input_stream.readline()
                break
            else:
                headers.append(line)

        # Extract signature if present
        cleaned_msg, sig_type, sig_data = extract_signature_from_message(commit_msg)

        # Write headers (mark, original-oid, author, committer, encoding)
        for header in headers:
            output_stream.write(header)

        # Write signature if we found one
        if sig_type and sig_data:
            signatures_restored += 1
            output_stream.write(b'gpgsig ' + sig_type + b'\n')
            output_stream.write(b'data %d\n' % len(sig_data))
            output_stream.write(sig_data)
            if not sig_data.endswith(b'\n'):
                output_stream.write(b'\n')

        # Write cleaned commit message
        output_stream.write(b'data %d\n' % len(cleaned_msg))
        output_stream.write(cleaned_msg)
        if not cleaned_msg.endswith(b'\n'):
            output_stream.write(b'\n')

        # Write the line after data block (might be 'from', 'merge', filemodify, or blank)
        output_stream.write(next_line)

        # Pass through rest of commit (file changes, etc.)
        while True:
            line = input_stream.readline()
            if not line:
                break

            output_stream.write(line)

            # Blank line signals end of commit
            if line == b'\n':
                break

    return commits_processed, signatures_restored


def restore_signatures(refs=None, verbose=False):
    """
    Restore GPG signatures from commit messages in the current repository.

    Args:
        refs: List of refs to process (default: all refs)
        verbose: Print progress information
    """
    if refs is None:
        refs = ['--all']

    if verbose:
        print(f"Exporting commits from refs: {' '.join(refs)}", file=sys.stderr)

    # Start git fast-export
    export_cmd = ['git', 'fast-export', '--show-original-ids'] + refs
    export_proc = subprocess.Popen(
        export_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    # Start git fast-import (use core.ignorecase=false to match git-filter-repo behavior)
    import_cmd = ['git', '-c', 'core.ignorecase=false', 'fast-import', '--force', '--quiet']
    import_proc = subprocess.Popen(
        import_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    # Process the stream
    try:
        commits_processed, signatures_restored = process_fast_export_stream(
            export_proc.stdout,
            import_proc.stdin
        )

        # Close pipes
        import_proc.stdin.close()
        export_proc.stdout.close()

        # Wait for completion
        export_returncode = export_proc.wait()
        import_returncode = import_proc.wait()

        # Check for errors
        if export_returncode != 0:
            stderr = export_proc.stderr.read()
            raise RuntimeError(f"git fast-export failed: {stderr.decode()}")

        if import_returncode != 0:
            stderr = import_proc.stderr.read()
            raise RuntimeError(f"git fast-import failed: {stderr.decode()}")

        if verbose:
            print(f"Processed {commits_processed} commits", file=sys.stderr)
            print(f"Restored {signatures_restored} signatures", file=sys.stderr)

        return signatures_restored

    except Exception as e:
        # Clean up processes
        export_proc.kill()
        import_proc.kill()
        raise


def main():
    parser = argparse.ArgumentParser(
        description='Restore GPG signatures from commit messages',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
This script is designed to work after git-filter-repo has been used with
signatures stored in commit messages by store_signatures.py. It
extracts signatures from messages (in the format
"original_gpgsig <type> <original-message-length>\\n<data>") and restores
them as proper git commit signatures.

Example workflow:
  1. Run git-filter-repo with signatures in messages
  2. Run this script to restore the signatures

Note: This rewrites history and should only be used on repositories where
you control all clones.
"""
    )

    parser.add_argument(
        '--refs',
        nargs='+',
        help='Refs to process (default: --all)'
    )

    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Print progress information'
    )

    args = parser.parse_args()

    try:
        signatures_restored = restore_signatures(
            refs=args.refs,
            verbose=args.verbose
        )

        if signatures_restored > 0:
            print(f"Successfully restored {signatures_restored} signature(s)")
            return 0
        else:
            if args.verbose:
                print("No signatures found to restore")
            return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
