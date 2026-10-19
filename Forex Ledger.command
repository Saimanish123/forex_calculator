#!/bin/bash
# Double-click to start Forex Ledger on macOS. It opens in your browser; to stop the app,
# close this Terminal window or press Ctrl+C.
cd "$(dirname "$0")" || exit 1

UV="$(command -v uv)"
for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
  if [ -z "$UV" ] && [ -x "$candidate" ]; then UV="$candidate"; fi
done

if [ -z "$UV" ]; then
  echo "Forex Ledger needs uv. Install it with this command in Terminal, then double-click this file again:"
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
  read -r -p "Press Enter to close. " _
  exit 1
fi

exec "$UV" run forex
