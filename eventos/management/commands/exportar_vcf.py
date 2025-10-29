import os
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from eventos.models import MailingList

# O vobject é uma biblioteca excelente e robusta para criar vCards.
# É provável que você precise instalá-la: pip install vobject
import vobject

class Command(BaseCommand):
    help = 'Exporta os contatos de uma MailingList para um arquivo .vcf universal (para Android e iOS)'

    def add_arguments(self, parser):
        parser.add_argument('mailing_list_id', type=int, help='O ID da MailingList a ser exportada.')

    def handle(self, *args, **options):
        mailing_list_id = options['mailing_list_id']

        try:
            mailing_list = MailingList.objects.get(pk=mailing_list_id)
        except MailingList.DoesNotExist:
            raise CommandError(f'MailingList com o ID "{mailing_list_id}" não foi encontrada.')

        self.stdout.write(self.style.SUCCESS(f'Iniciando a exportação da mailing list: "{mailing_list.nome}"...'))

        vcf_content = ""
        contatos = mailing_list.municipes.all()
        if not contatos.exists():
            self.stdout.write(self.style.WARNING('Esta mailing list não possui contatos para exportar.'))
            return

        for contato in contatos:
            card = vobject.vCard()

            # --- CORREÇÃO APLICADA AQUI ---
            # O campo correto é 'nome_completo', conforme o seu models.py
            card.add('fn').value = contato.nome_completo

            # --- CORREÇÃO APLICADA AQUI TAMBÉM ---
            card.add('n').value = vobject.vcard.Name(
                family=contato.nome_completo.split(' ')[-1] if ' ' in contato.nome_completo else '',
                given=contato.nome_completo.split(' ')[0] if ' ' in contato.nome_completo else contato.nome_completo
            )

            # --- O restante do código também precisa ser ajustado para os novos campos ---
            
            # Adicionando telefones do campo JSON 'telefones'
            if contato.telefones:
                # O campo 'telefones' é uma lista de dicionários, ex: [{'tipo': 'celular', 'numero': '119...'}]
                for tel_info in contato.telefones:
                    # Adicionamos uma verificação para garantir que 'numero' exista
                    if tel_info.get('numero'):
                        tel = card.add('tel')
                        tel.value = tel_info['numero']
                        # Define o tipo, se especificado, senão usa um padrão
                        tel.type_param = tel_info.get('tipo', 'CELL').upper()


            # Adicionando emails do campo JSON 'emails'
            if contato.emails:
                # O campo 'emails' é uma lista de dicionários, ex: [{'email': 'teste@teste.com'}]
                for email_info in contato.emails:
                    if email_info.get('email'):
                        email = card.add('email')
                        email.value = email_info['email']
                        email.type_param = 'INTERNET'

            vcf_content += card.serialize() + "\r\n"

        timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
        file_name = f"export_{mailing_list.nome.replace(' ', '_')}_{timestamp}.vcf"
        
        export_dir = 'exports'
        if not os.path.exists(export_dir):
            os.makedirs(export_dir)
            
        file_path = os.path.join(export_dir, file_name)

        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(vcf_content)
        except IOError as e:
            raise CommandError(f'Erro ao salvar o arquivo: {e}')

        self.stdout.write(self.style.SUCCESS(f'Exportação concluída com sucesso! {contatos.count()} contatos salvos em: "{file_path}"'))