#!/bin/bash
# ============================================================================
#  Delta-Wing CFD Studio – macOS başlatıcı
#  Finder'da çift tıklayın (veya Terminal'de: ./start_mac.command)
#  İlk açılışta Python sanal ortamı kurulur (~1-2 dk), sonra tarayıcı açılır.
# ============================================================================
cd "$(dirname "$0")" || exit 1

case "$PWD/" in
  "$HOME"/*) ;;
  *) echo "UYARI: Proje ev dizininizin (~) dışında. Docker/Colima CFD klasörlerine erişemeyebilir;"
     echo "       projeyi ~ altına taşımanız önerilir (örn. ~/Delta-Wing)." ;;
esac

# Homebrew ortamı (Apple Silicon: /opt/homebrew, Intel: /usr/local)
for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
  [ -x "$b" ] && eval "$("$b" shellenv)" && break
done

# En yeni uygun Python'u seç (3.9+)
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1; then
    if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then PY="$c"; break; fi
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.9+ bulunamadı."
  echo "Kurmak için: xcode-select --install   (veya Homebrew ile: brew install python)"
  read -r -p "Kapatmak için Enter'a basın..." _
  exit 1
fi
echo "Python: $($PY --version)"

if [ ! -x .venv/bin/python ]; then
  echo "Sanal ortam oluşturuluyor (.venv)…"
  "$PY" -m venv .venv || { echo "venv oluşturulamadı"; read -r _; exit 1; }
fi
# shellcheck disable=SC1091
source .venv/bin/activate

REQ_HASH=$(shasum requirements.txt | cut -d' ' -f1)
if [ "$(cat .venv/.req_hash 2>/dev/null)" != "$REQ_HASH" ]; then
  echo "Bağımlılıklar kuruluyor…"
  python -m pip install -q --upgrade pip
  python -m pip install -q -r requirements.txt || { echo "pip kurulumu başarısız"; read -r _; exit 1; }
  echo "$REQ_HASH" > .venv/.req_hash
fi

PORT="${PORT:-8765}"
if lsof -i :"$PORT" >/dev/null 2>&1; then
  echo "Uygulama zaten çalışıyor gibi görünüyor; tarayıcı açılıyor."
  open "http://127.0.0.1:$PORT"
  exit 0
fi
echo ""
echo "Delta-Wing CFD Studio başlatılıyor: http://127.0.0.1:$PORT"
echo "Kapatmak için bu pencerede Ctrl+C'ye basın."
echo ""
exec python -m app.server --port "$PORT"
