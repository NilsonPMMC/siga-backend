# 📝 Alterações: Assinatura Eletrônica em Ofícios

## ✅ Resumo das Alterações

Implementação de assinatura eletrônica para ofícios, permitindo que cada conta/gabinete tenha sua própria assinatura que será incluída automaticamente nos ofícios gerados.

## 🔧 Alterações Realizadas

### 1. Modelo `Conta` (`atendimentos/models.py`)

**Campos adicionados:**
- `assinatura_eletronica` (ImageField): Campo para upload da imagem da assinatura
  - Upload para: `assinaturas/`
  - Opcional (blank=True, null=True)
  
- `usar_assinatura_eletronica` (BooleanField): Checkbox para ativar/desativar o uso da assinatura
  - Padrão: `False`
  - Se marcado, a assinatura será incluída nos ofícios

### 2. View `GerarPdfOficioView` (`atendimentos/views.py`)

**Lógica adicionada:**
- Verifica se `conta.usar_assinatura_eletronica` está ativo
- Verifica se `conta.assinatura_eletronica` existe
- Se ambos forem verdadeiros, prepara a URL da assinatura usando `file://` para o WeasyPrint
- Passa `assinatura_url` no contexto do template

### 3. Template `oficio_template.html` (`oficios/templates/oficios/oficio_template.html`)

**Alterações no CSS:**
- Ajustado `.signature` para usar `flex-direction: column` e `align-items: center`
- Adicionado estilo para `.signature img`:
  - `max-width: 200pt`
  - `max-height: 60pt`
  - `margin-bottom: 8pt`

**Alterações no HTML:**
- Adicionada verificação `{% if assinatura_url %}` antes do nome do titular
- Imagem da assinatura é exibida acima do nome do titular quando disponível

### 4. Admin (`atendimentos/admin.py`)

**Novo fieldset adicionado:**
- Seção "Assinatura Eletrônica" no `ContaAdmin`
- Campos: `assinatura_eletronica` e `usar_assinatura_eletronica`
- Descrição explicativa sobre como usar

### 5. Migração (`atendimentos/migrations/0027_conta_assinatura_eletronica.py`)

**Criada migração** para adicionar os novos campos ao banco de dados.

## 📋 Como Usar

### 1. Aplicar Migração

No servidor, execute:
```bash
python manage.py migrate atendimentos
```

### 2. Configurar Assinatura no Admin

1. Acesse o Django Admin
2. Vá em **Atendimentos > Contas**
3. Edite a conta desejada
4. Na seção **"Assinatura Eletrônica"**:
   - Faça upload da imagem da assinatura (PNG ou JPG recomendado)
   - Marque o checkbox **"Usar Assinatura Eletrônica em Ofícios?"**
5. Salve

### 3. Gerar Ofício com Assinatura

Ao gerar um PDF de ofício:
- Se a conta tiver `usar_assinatura_eletronica = True` e uma imagem de assinatura:
  - A assinatura será exibida automaticamente acima do nome do titular
- Caso contrário:
  - Apenas o nome do titular será exibido (comportamento anterior)

## 🎨 Formato da Assinatura

**Recomendações:**
- Formato: PNG ou JPG
- Dimensões recomendadas: ~400x120 pixels (proporção ~3:1)
- Fundo transparente (PNG) ou branco
- Resolução: 300 DPI para melhor qualidade de impressão
- Tamanho máximo: 2MB

## 📁 Estrutura de Arquivos

```
media/
  └── assinaturas/
      ├── assinatura_gabinete_1.png
      ├── assinatura_gabinete_2.jpg
      └── ...
```

## ✅ Checklist de Implementação

- [x] Campos adicionados ao modelo `Conta`
- [x] View atualizada para passar URL da assinatura
- [x] Template atualizado para exibir assinatura
- [x] Admin configurado com novos campos
- [x] Migração criada
- [ ] **Aplicar migração no servidor** (`python manage.py migrate atendimentos`)
- [ ] Testar upload de assinatura no admin
- [ ] Testar geração de PDF com assinatura

## 🔍 Validações Implementadas

1. **Verificação de arquivo**: A view verifica se o arquivo existe fisicamente antes de usar
2. **Tratamento de erros**: Se houver erro ao carregar a assinatura, o sistema continua sem ela (não quebra)
3. **Checkbox obrigatório**: A assinatura só é exibida se o checkbox estiver marcado

## 📝 Notas Técnicas

- A URL da assinatura usa o formato `file://` para o WeasyPrint ler diretamente do disco
- A assinatura é exibida acima do nome do titular na seção de assinatura do ofício
- O tamanho máximo da imagem é controlado pelo Django (padrão: 2.5MB, configurável em `settings.py`)

## 🚀 Próximos Passos

1. Aplicar migração no servidor
2. Fazer upload das assinaturas eletrônicas de cada conta
3. Ativar o checkbox para as contas que devem usar assinatura
4. Testar geração de PDFs

---

**Data de Implementação**: 10/02/2026  
**Versão**: 1.0
