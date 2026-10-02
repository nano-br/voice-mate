# Amostras de avaliação STT (PT-BR)

Coloque aqui pares de arquivos para o `make stt-eval`:

- `<nome>.wav` — áudio PCM16 mono (qualquer sample rate; é reamostrado para 16 kHz).
  Grave frases naturais de 5–30 s, no seu microfone real, incluindo termos técnicos
  em inglês no meio do português (cenário de code-switching do dia a dia).
- `<nome>.ref.txt` — a transcrição de referência exata do que foi falado.

Workflow do gate de qualidade:

```bash
# 1. Na máquina de referência (main / NVIDIA), grave o baseline:
make stt-eval ARGS="--backends faster-whisper --save-baseline"

# 2. Em qualquer backend/máquina nova, compare:
make stt-eval ARGS="--backends whispercpp"
```

Critérios de aceitação: WER ≤ baseline + 1 p.p., zero palavras quebradas
("pa lavra"), e latência/init aceitáveis (tabela impressa no final).

Os `.wav` e o `baseline.json` são dados locais — não são commitados (ver `.gitignore`).
