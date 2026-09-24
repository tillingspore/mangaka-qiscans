"""Export the committed runtime and offline tests to Mangaka's build context."""
from pathlib import Path
import subprocess
import sys


def main():
    if len(sys.argv) != 2:
        raise SystemExit('Uso: python scripts/export.py DIRETORIO_DE_DESTINO')
    root = Path(__file__).resolve().parents[1]
    destination = Path(sys.argv[1]).resolve()
    if destination == root or root in destination.parents:
        raise SystemExit('Escolha um destino fora do repositório do wrapper.')
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    paths = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', revision], cwd=root, text=True).splitlines()
    included = [name for name in paths if name.startswith(('qiscans/', 'tests/'))
                or name in {'Dockerfile', '.dockerignore', 'pyproject.toml', 'LICENSE'}]
    for name in included:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(subprocess.check_output(['git', 'show', f'{revision}:{name}'], cwd=root))
    (destination / 'UPSTREAM.md').write_text(
        '# Origem do wrapper Qi Scans\n\n'
        f'Repositório: `mangaka-qiscans`\n\nCommit: `{revision}`\n\n'
        'Cópia gerada por `scripts/export.py`; atualize a partir do wrapper e faça commit no Mangaka.\n',
        encoding='utf-8')
    (destination / 'README.md').write_text(
        '# Integração Qi Scans\n\n'
        'Cópia versionada do wrapper `mangaka-qiscans`; origem em [UPSTREAM.md](UPSTREAM.md).\n\n'
        'O Compose do Mangaka constrói este diretório e disponibiliza a API em '
        '`http://qiscans:3002`, somente na rede interna. Os testes offline rodam durante o build.\n\n'
        'Para atualizar esta cópia, execute `scripts/export.py` no repositório do wrapper '
        'e faça commit no Mangaka. Não execute a exportação a partir desta cópia.\n',
        encoding='utf-8')
    print(f'Exportados {len(included)} arquivos do commit {revision[:7]} para {destination}')


if __name__ == '__main__':
    main()
