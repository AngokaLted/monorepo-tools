#!/usr/bin/env python3
"""
Store GPG signatures in commit messages.

This script processes a git repository and moves GPG signatures from commits
into the commit messages (appending "original_gpgsig <type> <len>\n<data>").
This allows signatures to survive git-filter-repo operations and be restored
later.

The original message length is recorded in the trailer so
restore_signatures.py can slice the message back out exactly, byte for
byte, regardless of how many trailing newlines the original message had.

Usage:
    store_signatures.py [--refs <refs>...]

If no refs are specified, processes all refs.
"""

import sys
import subprocess
import argparse


def process_fast_export_stream(input_stream, output_stream):
    """
    Process git fast-export stream, moving signatures into messages.
    """
    commits_processed = 0
    signatures_stored = 0

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
        sig_type = None
        sig_data = None
        commit_msg = None

        while True:
            line = input_stream.readline()

            if line.startswith(b'gpgsig '):
                # Extract signature type (everything after 'gpgsig ')
                sig_type = line[len(b'gpgsig '):].rstrip(b'\n')

                # Read signature data block
                data_line = input_stream.readline()
                if not data_line.startswith(b'data '):
                    raise ValueError(f"Expected 'data' after gpgsig, got: {data_line}")

                size = int(data_line.split()[1])
                sig_data = input_stream.read(size)

                # Consume trailing newline if present
                next_byte = input_stream.read(1)
                if next_byte != b'\n':
                    # This is unusual but handle it
                    sig_data += next_byte

            elif line.startswith(b'data '):
                # This is the commit message
                size = int(line.split()[1])
                commit_msg = input_stream.read(size)

                # Check for trailing newline
                next_line = input_stream.readline()
                break
            else:
                headers.append(line)

        # Write headers (mark, original-oid, author, committer, encoding)
        # Note: we skip the gpgsig header
        for header in headers:
            output_stream.write(header)

        # Build new message with signature appended.
        #
        # The trailer's boundary is defined solely by the recorded length of
        # the original message, not by how many trailing newlines it has.
        # This is deliberate: if the original message already ends in a
        # blank line (common for GitHub-generated merge commit bodies), a
        # newline-counting scheme can't tell "end of original message" apart
        # from "end of message's own trailing blank line", and silently
        # drops a byte on restore.
        new_msg = commit_msg
        if sig_type and sig_data:
            signatures_stored += 1
            new_msg += (b'\noriginal_gpgsig ' + sig_type + b' ' +
                        str(len(commit_msg)).encode() + b'\n' + sig_data)

        # Write new commit message
        output_stream.write(b'data %d\n' % len(new_msg))
        output_stream.write(new_msg)
        if not new_msg.endswith(b'\n'):
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

    return commits_processed, signatures_stored


def store_signatures(refs=None, verbose=False):
    """
    Store GPG signatures in commit messages in the current repository.

    Args:
        refs: List of refs to process (default: all refs)
        verbose: Print progress information
    """
    if refs is None:
        refs = ['--all']

    if verbose:
        print(f"Exporting commits from refs: {' '.join(refs)}", file=sys.stderr)

    # Start git fast-export with verbatim signatures
    export_cmd = ['git', 'fast-export', '--signed-commits=verbatim',
                  '--show-original-ids'] + refs
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
        commits_processed, signatures_stored = process_fast_export_stream(
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
            print(f"Stored {signatures_stored} signatures", file=sys.stderr)

        return signatures_stored

    except Exception as e:
        # Clean up processes
        export_proc.kill()
        import_proc.kill()
        raise


def main():
    parser = argparse.ArgumentParser(
        description='Store GPG signatures in commit messages',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
This script is designed to prepare a repository for git-filter-repo by moving
GPG signatures into commit messages. After filtering, use
restore_signatures.py to restore them.

Example workflow:
  1. Run this script to store signatures in messages
  2. Run git-filter-repo to filter the repository
  3. Run restore_signatures.py to restore the signatures

Note: This rewrites history and should only be used on repositories where
you control all clones.

Requirements:
  - Git 2.34+ with --signed-commits=verbatim support
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
        signatures_stored = store_signatures(
            refs=args.refs,
            verbose=args.verbose
        )

        if signatures_stored > 0:
            print(f"Successfully stored {signatures_stored} signature(s)")
            return 0
        else:
            if args.verbose:
                print("No signatures found to store")
            return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
