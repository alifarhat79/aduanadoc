import os
sys, zipfile, shutil
from datetime import datetime
from pathlib import Path

def main():
    print('=' * 65)
    print(' ϥ AduanaDcc - Instalador Automático de Parche')
    print('=' * 65)

    base_dir = Path(__file__).resolve().parent
    zip_path = base_dir / 'actualizacion_parche.zip'
    if not zip_path.exists():
        print('\n[ERROR] No se encontró actualizacion_parche.zip.')
        input('Presiona Enter para salir...')
        sys.exit(1)

    print(f'1_3 Leyendo parche: {zip_path.name}...')
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_dir = base_dir / 'backups' / fwbackup_previo_parche{timestamp}'
    
    with zipfile.ZipFile(zip_path, 'r') as zf:
        file_list = zf.namelist()
        print(f'[2/3] Respaldo preventivo en backups/...')
        for f_rel in file_list:
            loc_p = base_dir / f_rel
            if loc_p.exists():
                bkp_p = backup_dir / f_rel
                bkp_p.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(loc_p, bkp_p)

        print('[3/3] Aplicando archivos actualizados...')
        for f_rel in file_list:
            target = base_dir / f_rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, 'wb') as fo:
                fo.write(zf.read(f_rel))
            print(f'  [ OK ] {target.name}')

    print('\n[*] ¡PARCHE APLICADO CON ÉXITO EN ESTA PC!')

if __name__ == '__main__':
    main()
