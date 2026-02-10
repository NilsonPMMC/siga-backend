#!/usr/bin/env python
"""
Script para gerar uma nova SECRET_KEY do Django.
Execute: python gerar_secret_key.py
"""
import secrets
import string

def get_random_secret_key():
    """
    Gera uma SECRET_KEY segura para o Django.
    Baseado em django.core.management.utils.get_random_secret_key
    """
    chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    return ''.join(secrets.choice(chars) for i in range(50))

if __name__ == '__main__':
    secret_key = get_random_secret_key()
    print("\n" + "="*60)
    print("NOVA SECRET_KEY GERADA:")
    print("="*60)
    print(secret_key)
    print("="*60)
    print("\nCopie esta chave e cole no arquivo .env na variável SECRET_KEY")
    print("="*60 + "\n")
