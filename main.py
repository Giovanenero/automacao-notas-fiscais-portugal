import os, requests, json, base64
import time
from dotenv import load_dotenv
from textwrap import dedent
import fitz

load_dotenv()

OPEN_ROUTER_KEY = os.getenv('OPEN_ROUTER_KEY')
#MODEL_NAME = 'google/gemini-2.0-flash-001'
MODEL_NAME = 'google/gemini-3.1-flash-lite-preview'

DOLAR = 5.25

if not OPEN_ROUTER_KEY:
    print('Chave OPEN_ROUTER_KEY não encontrada no arquivo .env')
    exit(1)


HEADERS = {"Authorization": f"Bearer {OPEN_ROUTER_KEY}", "Content-Type": "application/json"}

def get_prompt():
    return dedent(f"""
        Você é um especialista em análise de dados de documentos.

        <objetivo>
        Seu objetivo é analisar o documento e extrair informações para alimentar um sistema de gestão seguindo fielmente as regras abaixo e o formato de resposta exigido.
        </objetivo>

        <regras>
        1. A análise deve ser feita exclusivamente com base no que está visível na imagem
        2. Não faça suposições ou use conhecimento externo
        3. Não faça inferência de dados.
        4. Se um campo não estiver claramente visível, retorne ""
        5. Valores monetários devem estar sem formatação e com ponto decimal. Exemplo: 1234.56
        6. Responder apenas em formato JSON
        7. Não escrever nada fora do JSON.
        8. Nos campos "data_emissao" e "data_vencimento" deixar no formato YYYY-MM-DD, mesmo que a data esteja em outro formato no documento.
        9. O campo "coordenadas_origem" do serviço ou produto deve conter latitude e longitude. Priorizar coordenadas do logradouro; se não estiver disponível, usar as coordenadas do centro da região.
        </regras>

        <formato_resposta>
        {{
            "resumo_geral": "resumo detalhado do documento",
            "valor_total": "valor total do serviço ou produto do documento",
            "valor_imposto": "valor total do imposto", 
            "codigo_moeda": "BRL | USD | EUR",
            "data_emissao": "data de emissão do documento, se houver",
            "data_vencimento": "data de vencimento do documento, se houver",
            "logradouro_origem": "logradouro de origem do serviço ou produto, se houver",
            "cidade_origem": "cidade de origem do serviço ou produto, se houver",
            "pais_origem": "país de origem do serviço ou produto, se houver",
            "coordenadas_origem": ["latitude", "longitude"],
            "produtos": [
                {{
                    "quantidade": "quantidade do produto ou serviço",
                    "nome_produto": "nome completo do produto",
                    "valor_total": "valor total do produto"
                }}    
            ],
            "nome_empresa": "nome da empresa que forneceu o serviço ou produto",
        }}
        </formato_resposta>
    """)


def pdf_to_base64(file_path:str) -> list[str]:
    try:

        doc = fitz.open(file_path)
        images = []

        for page in doc:
            pix = page.get_pixmap(dpi=300)
            img_bytes = pix.tobytes("png")

            images.append(base64.b64encode(img_bytes).decode())

        return images

    except Exception as e:
        raise Exception(f"Erro ao tentar converter pdf para base64 | {str(e)}")


def analyze_file(file_path:str):
    try:


        images_base64 = pdf_to_base64(file_path)

        content: list[dict] = [{
            'type': 'text',
            'text': get_prompt()
        }]

        images_base64 = [
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}
            } for image_base64 in images_base64
        ]

        content.extend(images_base64)

        messages = [
            {
                'role': 'user',
                'content': content
            }
        ]

        payload = {"model": MODEL_NAME, "messages": messages, "temperature": 0.0, "max_tokens": 800}
        response = requests.post('https://openrouter.ai/api/v1/chat/completions', headers=HEADERS, data=json.dumps(payload))
        response.raise_for_status()
        response_json = response.json()
        message = response_json.get('choices', [{}])[0].get('message', {}).get('content', '{}')
        data = json.loads(message.replace('```json', '').replace('```', ''))
        cost = response_json.get('usage', {}).get('cost', None)
        cost = float(cost) * DOLAR if cost else None


        data['nome_arquivo'] = os.path.basename(file_path)
        data['custo'] = cost

        return data

    except Exception as e:
        print(f'Erro ao analisar o arquivo: {file_path} | {str(e)}')

    return None


def run():

    file_names = os.listdir('files')
    paths = [os.path.join('files', file_name) for file_name in file_names]
    result = []

    for file_path in paths: 

        if not os.path.exists(file_path):
            print(f'Arquivo não encontrado: {file_path}')
            continue

        start_time = time.time()

        data = analyze_file(file_path)

        if not data:
            print(f'Não foi possível analisar o arquivo: {file_path}')
            continue

        data['tempo_processamento'] = int(round(time.time() - start_time, 0))

        result.append(data)

    with open('result.json', 'w') as f:
        json.dump(result, f, indent=4, ensure_ascii=False)

    

if __name__ == '__main__':
    run()