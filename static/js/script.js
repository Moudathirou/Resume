let mediaRecorder;
        let audioChunks = [];
        let isRecording = false;
        let sessionId = null;
        let transcriptionCheckInterval = null;
        let summaryCheckInterval = null;

       

        // Gestionnaire pour le bouton d'enregistrement
        const recordButton = document.getElementById('recordButton');
        recordButton.addEventListener('click', toggleRecording);

        async function toggleRecording() {
            if (!isRecording) {
                try {
                    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                    mediaRecorder = new MediaRecorder(stream);
                    audioChunks = [];

                    mediaRecorder.ondataavailable = event => {
                        audioChunks.push(event.data);
                    };

                    mediaRecorder.onstop = async () => {
                        const audioBlob = new Blob(audioChunks, { type: 'audio/wav' });
                        await handleAudioFile(audioBlob);
                    };

                    mediaRecorder.start();
                    isRecording = true;
                    recordButton.textContent = 'Arrêter l\'enregistrement';
                    recordButton.classList.add('recording');
                } catch (err) {
                    console.error('Erreur lors de l\'accès au microphone:', err);
                    alert('Impossible d\'accéder au microphone');
                }
            } else {
                mediaRecorder.stop();
                isRecording = false;
                recordButton.textContent = 'Commencer l\'enregistrement';
                recordButton.classList.remove('recording');
            }
        }

        // Fonction pour gérer le fichier audio (enregistrement ou upload)
        async function handleAudioFile(audioBlob) {
            const formData = new FormData();
            formData.append('audio_file', audioBlob, 'audio_input.wav');

            try {
                const transcriptionOutput = document.getElementById('transcription-output');
                transcriptionOutput.value = 'Transcription en cours...';

                const response = await fetch('/transcription', {
                    method: 'POST',
                    body: formData,
                    credentials: 'include'
                });

                
                const data = await response.json();

                if (data.error) {
                    throw new Error(data.error);
                }

                if (data.status === 'processing') {
                    startTranscriptionCheck();
                }
            } catch (error) {
                console.error('Erreur:', error);
                transcriptionOutput.value = `Erreur: ${error.message}`;
            }
        }

        // Gestionnaire pour le fichier uploadé
        document.getElementById('transcriptionAudio').addEventListener('change', async function(e) {
            const file = e.target.files[0];
            if (!file) return;

            const audioContainer = document.getElementById('transcription-audio-container');
            audioContainer.innerHTML = '';
            const audio = document.createElement('audio');
            audio.controls = true;
            audio.src = URL.createObjectURL(file);
            audioContainer.appendChild(audio);

            const formData = new FormData();
            formData.append('audio_file', file);

            try {
                const transcriptionOutput = document.getElementById('transcription-output');
                transcriptionOutput.value = 'Transcription en cours...';

                const response = await fetch('/transcription', {
                    method: 'POST',
                    body: formData,
                    credentials: 'include' // Important pour maintenir la session
                });
                const data = await response.json();

                if (data.error) {
                    throw new Error(data.error);
                }

                if (data.status === 'processing') {
                    startTranscriptionCheck();
                }
            } catch (error) {
                console.error('Erreur:', error);
                transcriptionOutput.value = `Erreur: ${error.message}`;
            }
        });

        // Fonctions pour vérifier le statut de la transcription
        function startTranscriptionCheck() {
            if (transcriptionCheckInterval) {
                clearInterval(transcriptionCheckInterval);
            }
            transcriptionCheckInterval = setInterval(checkTranscriptionStatus, 2000);
        }

        async function checkTranscriptionStatus() {
            try {
                const response = await fetch('/check-transcription', {
                    credentials: 'include'
                });
                const data = await response.json();

                if (data.status === 'completed') {
                    clearInterval(transcriptionCheckInterval);
                    document.getElementById('transcription-output').value = data.transcription;
                    document.getElementById('summarize-btn').classList.remove('hidden');
                } else if (data.status === 'error') {
                    clearInterval(transcriptionCheckInterval);
                    document.getElementById('transcription-output').value = `Erreur: ${data.error}`;
                }
            } catch (error) {
                console.error('Erreur lors de la vérification de la transcription:', error);
            }
        }

        async function summarizeTranscription() {
            const transcriptionText = document.getElementById('transcription-output').value;
            const summaryOutput = document.getElementById('summary-output');
            const itemsOutput = document.getElementById('items-output');

            summaryOutput.classList.remove('hidden');
            itemsOutput.classList.remove('hidden');
            summaryOutput.value = 'Analyse en cours...';
            itemsOutput.value = 'Extraction des éléments clés...';

            try {
                const response = await fetch('/summarize', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        transcription_text: transcriptionText
                    }),
                    credentials: 'include'
                });
                const data = await response.json();

                if (data.error) {
                    throw new Error(data.error);
                }

                // Mettre à jour les champs avec les données reçues
                summaryOutput.value = data.summary;
                itemsOutput.value = data.key_elements || 'Aucun élément clé trouvé';

                // Remplir automatiquement le contenu de l'email
                document.getElementById('emailContent').value = data.email_content;

              
            } catch (error) {
                summaryOutput.value = `Erreur: ${error.message}`;
                itemsOutput.value = 'Erreur lors de l\'extraction des éléments clés';
            }
        }


        // Ajouter cet événement au bouton de résumé
        document.getElementById('summarize-btn').addEventListener('click', summarizeTranscription);

        // Fonctions pour vérifier le statut du résumé
        function startSummaryCheck() {
            if (summaryCheckInterval) {
                clearInterval(summaryCheckInterval);
            }
            summaryCheckInterval = setInterval(checkSummaryStatus, 2000);
        }

        async function checkSummaryStatus() {
            try {
                const response = await fetch('/check-summary', {
                    credentials: 'include'
                });
                const data = await response.json();

                if (data.status === 'completed') {
                    clearInterval(summaryCheckInterval);
                    const summaryParts = data.summary.split('\n\nÉléments clés:\n');
                    document.getElementById('summary-output').value = summaryParts[0];
                    document.getElementById('items-output').value = summaryParts[1] || 'Aucun élément clé trouvé';
                    
                } else if (data.status === 'error') {
                    clearInterval(summaryCheckInterval);
                    document.getElementById('summary-output').value = `Erreur: ${data.error}`;
                    document.getElementById('items-output').value = 'Erreur lors de l\'extraction des éléments clés';
                }
            } catch (error) {
                console.error('Erreur lors de la vérification du résumé:', error);
            }
        }

        document.getElementById('sendEmail').addEventListener('click', async function() {
            const senderEmail = document.getElementById('senderEmail').value;
            const recipients = document.getElementById('emailRecipients').value;
            const subject = document.getElementById('emailSubject').value;
            const content = document.getElementById('emailContent').value;

            if (!senderEmail || !recipients || !subject || !content) {
                alert('Veuillez remplir tous les champs de l\'email');
                return;
            }

            try {
                const response = await fetch('/send-email', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        sender_email: senderEmail,
                        recipients: recipients.split(',').map(email => email.trim()),
                        subject: subject,
                        content: content
                    }),
                    credentials: 'include'
                });
                const data = await response.json();

                if (data.error) {
                    throw new Error(data.error);
                }

                alert('Email envoyé avec succès !');
            } catch (error) {
                console.error('Erreur:', error);
                alert(`Erreur lors de l'envoi de l'email: ${error.message}`);
            }
        });

