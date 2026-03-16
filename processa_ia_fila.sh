#!/bin/bash

cd /var/www/gabinete/siga-gabinete
. venv/bin/activate

python manage.py processar_ia_atendimentos --limite 50
sleep 30
python manage.py processar_ia_municipes --limite 50