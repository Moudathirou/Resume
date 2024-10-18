from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import os
from werkzeug.utils import secure_filename
import openai
from groq import Groq
from moviepy.editor import VideoFileClip
from dotenv import load_dotenv
import tempfile
from flask_cors import CORS
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask_session import Session
import uuid
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
import threading
import re
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from sqlalchemy import func

load_dotenv()
app = Flask(__name__)
CORS(app, supports_credentials=True)

# Configuration de la session
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'votre_clé_secrète_par_défaut')
app.config['UPLOAD_FOLDER'] = os.path.join(tempfile.gettempdir(), 'transcription_uploads')
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 465))
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER')
app.config['STATIC_KEY'] = os.getenv('STATIC_KEY', 'votre_cle_statique')

# Configure SQLAlchemy
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Initialiser la session
Session(app)

# Définir le modèle User
class User(db.Model):
    id = db.Column(db.String, primary_key=True)
    full_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    request_count = db.Column(db.Integer, default=0)
    last_request_date = db.Column(db.DateTime, default=datetime.utcnow)

    @classmethod
    def get_or_create(cls, user_id, full_name, email):
        user = cls.query.get(user_id)
        if user is None:
            user = cls(id=user_id, full_name=full_name, email=email)
            db.session.add(user)
            db.session.commit()
        return user

# Créer les tables
with app.app_context():
    db.create_all()

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Initialisation des clients API
openai.api_key = os.getenv('API_KEY')
groq_client = Groq(api_key=os.getenv('GROQ_API'))

# Pool d'exécution pour gérer plusieurs requêtes simultanément
executor = ThreadPoolExecutor(max_workers=3)

# Dictionnaire pour stocker les tâches en cours
active_tasks = {}

# Extensions de fichiers autorisées
ALLOWED_EXTENSIONS = {'wav', 'mp3', 'm4a', 'mp4', 'avi', 'mov'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS



def save_file(file):
    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)
    return filepath

def extract_audio_from_video(video_path, audio_path):
    video = VideoFileClip(video_path)
    video.audio.write_audiofile(audio_path)
    return audio_path

def process_audio(input_filepath, user_id):
    audio_filepath = input_filepath
    try:
        # Si c'est une vidéo, extraire l'audio
        if input_filepath.lower().endswith(('.mp4', '.avi', '.mov')):
            audio_filepath = os.path.join(os.path.dirname(input_filepath), f'audio_{user_id}.mp3')
            extract_audio_from_video(input_filepath, audio_filepath)

        # Transcrire l'audio
        transcription_text = transcribe_audio(audio_filepath)
        
        return transcription_text
    except Exception as e:
        app.logger.error(f"Erreur lors du traitement audio pour l'utilisateur {user_id}: {e}")
        raise
    finally:
        # Nettoyer les fichiers
        cleanup_files(input_filepath)
        if audio_filepath != input_filepath:
            cleanup_files(audio_filepath)


def cleanup_files(*filepaths):
    for filepath in filepaths:
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception as e:
            app.logger.error(f"Erreur lors du nettoyage du fichier {filepath}: {e}")

def transcribe_audio(filepath):
    try:
        with open(filepath, "rb") as audio_file:
            transcription = groq_client.audio.transcriptions.create(
                file=(os.path.basename(filepath), audio_file.read()),
                model="whisper-large-v3",
                response_format="verbose_json",
                temperature=0.0
            )
        
        # Formater la transcription en texte
        segments = []
        for segment in transcription.segments:
            start_time = segment.get('start', 0)
            end_time = segment.get('end', 0)
            text = segment.get('text', '')
            segments.append(f"[{start_time:.2f} - {end_time:.2f}] {text}")
        
        return "\n".join(segments)
    except Exception as e:
        app.logger.error(f"Erreur lors de la transcription : {e}")
        raise

def generate_summary(text):
    try:
        response = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": """Vous êtes un assistant spécialisé dans l'analyse et le résumé de transcriptions.
                 Fournissez un résumé concis suivi d'une liste d'éléments clés.
                 Format de réponse :
                 [Résumé en un paragraphe]

                 Éléments clés:
                 • Point clé 1
                 • Point clé 2
                 [etc.]"""},
                {"role": "user", "content": f"Analysez et résumez la transcription suivante :\n\n{text}"}
            ],
            max_tokens=500
        )
        return response.choices[0].message.content
    except Exception as e:
        app.logger.error(f"Erreur lors de la génération du résumé : {e}")
        raise

def generate_email_report(summary, key_elements):
    try:
        # Préparer le prompt pour l'API OpenAI
        prompt = f"""
        Vous êtes un assistant qui aide à rédiger des emails professionnels pour des rendez-vous immobiliers. 

        Basé sur le résumé suivant et les éléments clés, rédigez un email de suivi professionnel pour le client :

        Résumé :
        {summary}

        Éléments clés :
        {key_elements}

        L'email doit être poli, clair et adapté au contexte immobilier.
        """

        response = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Vous êtes un assistant d'email professionnel."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=500
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        app.logger.error(f"Erreur lors de la génération du rapport d'email : {e}")


def reset_daily_count():
    yesterday = datetime.utcnow() - timedelta(days=1)
    users_to_reset = User.query.filter(User.last_request_date < yesterday).all()
    for user in users_to_reset:
        user.request_count = 0
        user.last_request_date = datetime.utcnow()
    db.session.commit()

@app.before_request
def before_request():
    reset_daily_count()

def increment_request_count(user_id):
    user = User.query.get(user_id)
    if user is None:
        app.logger.error(f"User {user_id} not found when incrementing request count")
        return None
    user.request_count += 1
    user.last_request_date = datetime.utcnow()
    db.session.commit()
    return user.request_count

def decrement_request_count(user_id):
    user = User.query.get(user_id)
    if user is None:
        app.logger.error(f"User {user_id} not found when decrementing request count")
        return None
    if user.request_count > 0:
        user.request_count -= 1
    db.session.commit()
    return user.request_count

def check_request_limit(user_id):
    user = User.query.get(user_id)
    if user is None:
        app.logger.error(f"User {user_id} not found when checking request limit")
        return False
    return user.request_count < 5





# Fonction pour vérifier si l'utilisateur est connecté
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Non autorisé'}), 401
        return f(*args, **kwargs)
    return decorated_function




@app.route('/', methods=['GET'])
def index():
    email = request.args.get('email')
    key = request.args.get('key')

    STATIC_KEY = app.config['STATIC_KEY']

    if not email or not key:
        return jsonify({'error': 'Email et clé requis'}), 400

    if key != STATIC_KEY:
        return jsonify({'error': 'Clé invalide'}), 401

    # Vérifier si l'utilisateur existe, sinon le créer
    user = User.query.filter_by(email=email).first()
    if not user:
        user_id = str(uuid.uuid4())
        full_name = email.split('@')[0]  # Vous pouvez adapter cela selon vos besoins
        user = User(id=user_id, full_name=full_name, email=email)
        db.session.add(user)
        db.session.commit()
    else:
        user_id = user.id
        full_name = user.full_name

    # Authentifier l'utilisateur en définissant les variables de session
    session['user_id'] = user_id
    session['full_name'] = full_name

    return render_template('transcription.html')




@app.route('/transcription', methods=['POST'])
@login_required
def transcription():
    user_id = session.get('user_id')
    
    try:
        # Check request limit
        if not check_request_limit(user_id):
            return jsonify({'error': 'Daily request limit reached'}), 429
        
        # Increment the request count
        request_count = increment_request_count(user_id)
        if request_count is None:
            raise ValueError("Failed to increment request count")
        
        if 'audio_file' not in request.files:
            raise ValueError('No file provided')
        
        file = request.files['audio_file']
        if file.filename == '':
            raise ValueError('No file selected')
        
        if not allowed_file(file.filename):
            raise ValueError('File type not allowed')

        # Create a unique folder for the user
        user_folder = os.path.join(app.config['UPLOAD_FOLDER'], user_id)
        os.makedirs(user_folder, exist_ok=True)

        # Save the file
        input_filepath = os.path.join(user_folder, secure_filename(file.filename))
        file.save(input_filepath)
        
        # Launch transcription asynchronously
        future = executor.submit(process_audio, input_filepath, user_id)
        active_tasks[user_id] = future
        
        remaining_requests = max(0, 5 - request_count)
        
        return jsonify({
            'task_id': user_id,
            'status': 'processing',
            'remaining_requests': remaining_requests
        })

    except Exception as e:
        app.logger.error(f"Error during processing for user {user_id}: {e}")
        User.decrement_request_count(user_id)
        return jsonify({'error': str(e)}), 500


@app.route('/check-transcription', methods=['GET'])
def check_transcription():
    user_id = session.get('user_id')
    if not user_id or user_id not in active_tasks:
        return jsonify({'status': 'not_found'})

    future = active_tasks[user_id]
    if future.done():
        try:
            result = future.result()
            del active_tasks[user_id]
            return jsonify({
                'status': 'completed',
                'transcription': result
            })
        except Exception as e:
            del active_tasks[user_id]
            return jsonify({
                'status': 'error',
                'error': str(e)
            })
    return jsonify({'status': 'processing'})



@app.route('/summarize', methods=['POST'])
def summarize():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'Session invalide'}), 401

    data = request.get_json()
    if not data or 'transcription_text' not in data:
        return jsonify({'error': 'Aucun texte de transcription fourni'}), 400

    transcription_text = data['transcription_text']
    
    try:
        # Générer le résumé et les éléments clés
        summary = generate_summary(transcription_text)
        
        # Séparer le résumé et les éléments clés
        summary_parts = summary.split('\n\nÉléments clés:\n')
        summary_text = summary_parts[0]
        key_elements = summary_parts[1] if len(summary_parts) > 1 else ''

        # Générer le rapport d'email
        email_content = generate_email_report(summary_text, key_elements)

        return jsonify({
            'status': 'completed',
            'summary': summary_text,
            'key_elements': key_elements,
            'email_content': email_content
        })
    except Exception as e:
        app.logger.error(f"Erreur lors de la génération du résumé pour l'utilisateur {user_id}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/check-summary', methods=['GET'])
def check_summary():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'status': 'not_found'})

    task_id = f"{user_id}_summary"
    if task_id not in active_tasks:
        return jsonify({'status': 'not_found'})

    future = active_tasks[task_id]
    if future.done():
        try:
            result = future.result()
            del active_tasks[task_id]
            return jsonify({
                'status': 'completed',
                'summary': result
            })
        except Exception as e:
            del active_tasks[task_id]
            return jsonify({
                'status': 'error',
                'error': str(e)
            })
    return jsonify({'status': 'processing'})





def is_valid_email(email):
    email_regex = r'^[\w\.-]+@[\w\.-]+\.\w+$'
    return re.match(email_regex, email) is not None

@app.route('/send-email', methods=['POST'])
def send_email():
    data = request.get_json()
    if not data or 'sender_email' not in data or 'recipients' not in data or 'subject' not in data or 'content' not in data:
        return jsonify({'error': 'Données email manquantes'}), 400

    sender_email = data['sender_email']
    recipients = data['recipients']
    subject = data['subject']
    content = data['content']

    # Vérifier la validité de l'email de l'utilisateur
    if not is_valid_email(sender_email):
        return jsonify({'error': 'Adresse email invalide'}), 400

    try:
        # Création du message
        msg = MIMEMultipart()
        msg['From'] = app.config['MAIL_DEFAULT_SENDER']
        msg['To'] = ', '.join(recipients)
        msg['Subject'] = subject
        msg['Reply-To'] = sender_email

        # Ajout du contenu
        msg.attach(MIMEText(content, 'plain'))

        # Connexion au serveur SMTP et envoi
        with smtplib.SMTP_SSL(app.config['MAIL_SERVER'], app.config['MAIL_PORT']) as server:
            server.login(app.config['MAIL_USERNAME'], app.config['MAIL_PASSWORD'])
            server.send_message(msg)

        return jsonify({'message': 'Email envoyé avec succès'})
    except Exception as e:
        app.logger.error(f"Erreur lors de l'envoi de l'email : {e}")
        return jsonify({'error': str(e)}), 500

    







@app.errorhandler(Exception)
def handle_error(error):
    app.logger.error(f"Erreur non gérée : {error}")
    return jsonify({'error': 'Une erreur inattendue est survenue'}), 500





if __name__ == '__main__':
    app.run(host="0.0.0.0", port=8080)

















