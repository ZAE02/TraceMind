"""Download only Qwen's public tokenizer, never model weights."""
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen
ROOT=Path(__file__).resolve().parent
destination=ROOT/'tokenizer/tokenizer.json'
destination.parent.mkdir(exist_ok=True)
url='https://huggingface.co/Qwen/Qwen3.8-27B/resolve/main/tokenizer.json'
if not destination.exists():
    with urlopen(url,timeout=120) as response:
        content=response.read()
    json.loads(content.decode('utf-8'))
    destination.write_bytes(content)
else:content=destination.read_bytes()
(destination.parent/'source.json').write_text(json.dumps({'url':url,'sha256':hashlib.sha256(content).hexdigest(),
    'bytes':len(content),'scope':'Public tokenizer only; hosted tokenizer identity may differ and is not assumed identical.'},indent=2),encoding='utf-8')
print('Tokenizer bytes:',len(content))
