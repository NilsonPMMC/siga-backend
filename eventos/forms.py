from django import forms

class ListaPresencaForm(forms.Form):
    nome_completo = forms.CharField(label="Nome Completo", max_length=255)
    data_nascimento = forms.CharField(label="Data de Nascimento (DD/MM)", max_length=5, required=False)
    telefone = forms.CharField(label="Telefone (com DDD)", max_length=20)
    email = forms.EmailField(label="E-mail (opcional, para receber o certificado)", required=False)
    instituicao_orgao = forms.CharField(label="Instituição/Órgão", max_length=255, required=False)