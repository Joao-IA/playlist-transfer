# music-transfer

Transfere playlists do Spotify para o YouTube Music a partir dos CSVs exportados pelo [Exportify](https://exportify.app).
Cada CSV vira uma playlist privada no YT Music com o mesmo nome. O Exportify troca espaços por `_` no nome do arquivo, então `Rock_Foda.csv` vira "Rock Foda".

## 1. Instalar

```
pip install -r requirements.txt
```

## 2. Login no YT Music (uma vez)

1. Abra https://music.youtube.com no Chrome/Edge, logado na sua conta.
2. Aperte `F12` → aba **Rede** (Network) → no filtro digite `browse`.
3. Clique em **Biblioteca** no YT Music para gerar uma requisição. Clique na requisição `browse?...` do tipo **POST**.
4. Em **Cabeçalhos** (Headers) → **Cabeçalhos de solicitação** (Request Headers), selecione tudo e copie.
   No Chrome, ative "Raw"/"Bruto" antes se a opção existir.
5. Cole em um arquivo `headers.txt` nesta pasta e rode:

```
python -m transfer login
```

Isso cria o `browser.json` e apaga o `headers.txt`. O login vale enquanto você não sair da conta nesse navegador.
**Não compartilhe o `browser.json`**: ele dá acesso à sua conta.

## 3. Transferir

```
python -m transfer list                                   # playlists encontradas nos CSVs
python -m transfer run --playlist running --dry-run       # só testa a busca, sem criar nada
python -m transfer run --playlist running                 # transfere uma playlist
python -m transfer run                                    # transfere todas
```

- **Retomar:** se parar no meio (Ctrl+C, erro, internet), rode o mesmo comando de novo. As buscas ficam em `state/` e as músicas que já estão na playlist não são adicionadas de novo.
- **Nome diferente:** `python -m transfer run --playlist Rock_Foda --name "Nome exato"`. Se a playlist já foi criada, ela é renomeada.
- **Buscar de novo:** `--rematch` ignora o cache de buscas.

## Relatórios

Cada execução gera `output/<playlist>.csv`, que abre direto no Excel:

| status | significado |
|---|---|
| `matched` | encontrada com segurança (nota ≥ 90) |
| `low_confidence` | adicionada, mas vale conferir (nota 80–89) |
| `not_found` | não adicionada; o link mostra o candidato mais próximo, para conferir |
| `ignorada` | linha inválida no CSV (faixa removida do Spotify ou arquivo local) |

As músicas `not_found` podem ser adicionadas manualmente pelo app do YT Music.
