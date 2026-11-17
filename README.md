<img src="assets/icon.svg" width="64" height="64" alt="">

# Écoute intelligente

Enregistrement de consultation ou de RCP → **transcription 100 % locale** (Whisper sur puce Apple) → **anonymisation locale** → **relecture humaine obligatoire** → rédaction du CR, des actions et des brouillons de mails par Claude.

> [!WARNING]
> Mode consultation : envoi **automatique** à Claude après anonymisation locale, sans relecture (choix assumé). Pour les autres circuits, rien ne part vers Claude tant que **vous** n'avez pas relu la note et ne l'avez pas déplacée dans `3_Valide`. L'anonymisation automatique masque les noms précédés d'une civilité, les dates complètes, les IPP/NIP/INS (même dictés en lettres), les téléphones et les e-mails, mais **elle n'est pas infaillible** : un nom prononcé seul (« le dossier Lebrun ») est seulement *signalé*, pas masqué.

## Installation (MacBook Air ou Mac mini, Apple Silicon)

```bash
git clone https://github.com/mrthomaslef/ecoute-intelligente.git ~/ecoute-intelligente
cd ~/ecoute-intelligente && ./install.sh
```

Le script installe ffmpeg, Whisper (MLX), précharge une fois le modèle `large-v3-turbo` (≈1,6 Go) et active en arrière-plan la surveillance automatique des dossiers `~/EcouteIntelligente`. Ensuite, tout fonctionne hors ligne, sauf l'étape Claude.

## En consultation : écoute en direct

**Un bouton dans le Dock** : cliquez sur 🟣 **Écoute Consultation** pour démarrer l'écoute, parlez, puis **recliquez pour terminer**. Tout le reste est automatique :

1. transcription finale locale de tout l'enregistrement ;
2. identification du patient dans l'**Agenda RI Cochin** (onglet Consultations du jour) : nom entendu pendant la consultation, sinon votre créneau horaire le plus proche (± 2 h) ;
3. anonymisation locale (y compris les noms de tous les patients du jour) puis rédaction du CR de consultation par Claude ;
4. réinsertion locale de l'identité et **dépôt du CR dans la fiche du patient** (« Remarques de suivi » / « Suivi clinique & recommandations CS »), à la suite de ce qui y figurait déjà. Une notification confirme le patient.

**Organisation conseillée (depuis le 16/11/2026) : le MacBook enregistre, le Mac mini transcrit TOUT.** En mode « enregistreur », le MacBook ne lance plus Whisper en direct (trop lourd pour lui) : il enregistre, détecte seulement la parole, et à la fin envoie l'audio (consultation **et RCP**) au Mac mini. Le Mac mini fait la transcription précise, le CR (consultation) ou la synthèse dossier par dossier (RCP). Une écoute coupée (veille, plantage) restée dans `1_Audio/_en_cours` est reprise automatiquement au bout de 10 min. Le MacBook ne traite lui-même un enregistrement qu'en dernier recours, si le Mac mini ne l'a pas pris en 30 min.

**MacBook enregistreur → Mac mini (relais iCloud)** : sur le MacBook (`echo enregistreur > ~/EcouteIntelligente/.mode`), l'écoute ne fait qu'enregistrer ; à la fin, l'audio part dans iCloud Drive › EcouteIntelligente › Relais. Le Mac mini (mode par défaut « traitement », surveillance en arrière-plan) le récupère, fait la transcription finale, lit le dossier antérieur dans Google Drive, fait rédiger le CR et le dépose dans la fiche du patient de l'Agenda RI (bouton copier de l'agenda). L'audio quitte iCloud dès sa prise en charge.

**Automatique toute la matinée** : chaque transcription anonymisée est aussi ajoutée à `~/EcouteIntelligente/Transcriptions/AAAA-MM-JJ.md` (une section par consultation, avec le patient et le créneau). Si le MacBook est en mode « enregistreur » et qu'un enregistrement n'est pas pris par le Mac mini en 30 minutes (iCloud bloqué, Mac mini éteint), le MacBook le traite lui-même (lent, dernier recours). Le Mac mini, lui, ne se met plus en veille tant que la surveillance tourne.

**Dossier antérieur (Google Drive)** : avant chaque patient, copiez son dossier précédent (Google Doc, Word, texte ou PDF) dans **Drive › 1sceenshot et texte com › Consultation JJ/MM**. À la fin de l'écoute, le document **au nom du patient** (nom dans le titre ou le texte) est lu ; **aucun document à son nom = première consultation** (jamais le dossier d'un autre patient). Il est lu, anonymisé localement puis joint à la transcription : le CR reprend l'histoire, les antécédents et l'imagerie antérieure. Le CR final (identité réinsérée) est **copié dans le presse-papiers (⌘V)** et s'ouvre (`4_CR_Prets/…_a_copier.txt`). Prérequis : `brew install rclone` puis une fois `rclone config create gdrive drive scope=drive.readonly` (autoriser dans le navigateur).

Pendant toute l'écoute (consultation ou RCP), puis la transcription finale et la rédaction du CR, **le Mac ne se met pas en veille et l'écran reste allumé** ; la veille normale revient d'elle-même à la fin. ⚠️ Sur batterie, refermer l'écran met quand même le MacBook en veille : laissez-le ouvert.

Si le patient n'est pas trouvé ou si l'agenda est inaccessible, le CR s'ouvre et reste dans `4_CR_Prets`. 🟢 **Écoute RCP** garde l'ancien circuit (note à relire puis `3_Valide`). Le code d'accès de l'agenda est demandé une seule fois par Mac (Trousseau) ; `ecoute agenda` vérifie l'accès.

En ligne de commande :

```bash
ecoute consultation            # CR de consultation (Ctrl+C pour terminer)
ecoute consultation --type rcp # RCP
ecoute consultation --precis   # passe finale avec large-v3 complet (plus lent, plus juste)
ecoute micros                  # choisir un micro externe : --micro <numéro>
```

1. **Pendant** l'échange, chaque phrase s'affiche environ 1 seconde après avoir été prononcée. L'audio est sauvegardé en continu sur le Mac.
2. **À la fin** (Ctrl+C), tout l'enregistrement est retranscrit une seconde fois en une seule passe, avec le contexte complet, ce qui est plus juste que le direct. La note anonymisée s'ouvre ensuite pour relecture.
3. Une fois la note relue, déplacez-la dans `3_Valide` : la surveillance en arrière-plan détecte le fichier et Claude rédige automatiquement le CR, les points de vigilance et le courrier au confrère dans `4_CR_Prets`.

> [!TIP]
> Pour une transcription la plus juste possible :
> - Posez le MacBook entre vous et le patient, ou utilisez un petit micro-cravate ou un micro de table.
> - Ajoutez vos termes dans `vocabulaire.txt` (molécules, techniques, noms de correspondants).
> - Notez dans `corrections.txt` les erreurs récurrentes, par exemple `cardégique = Kardégic`.
> - Au premier lancement, macOS demande l'accès au micro pour le Terminal : cliquez sur **Autoriser**.

## Fichiers déposés (dictaphone, iPhone)

```bash
ecoute dossiers          # ouvrir les dossiers dans le Finder
ecoute surveiller        # (déjà lancé automatiquement en arrière-plan par install.sh)
```

| Dossier (`~/EcouteIntelligente`) | Rôle |
|---|---|
| `1_Audio/CR`, `1_Audio/RCP` | Déposer l'enregistrement (.m4a, .mp3, .wav…) |
| `2_A_Relire` | Transcription anonymisée : **relire, corriger, effacer les noms restants** |
| `3_Valide` | Déplacer ici la note relue : elle part automatiquement chez Claude |
| `4_CR_Prets` | CR, liste d'actions et brouillons de mails |
| `noms_a_masquer.txt` | Noms à toujours masquer (reste sur le Mac) |
| `vocabulaire.txt`, `corrections.txt` | Vocabulaire et corrections personnelles de transcription |

Commandes ponctuelles : `ecoute transcrire fichier.m4a --type rcp` et `ecoute rediger note.md`.

Les trames de rédaction se modifient dans `prompts/cr.md` et `prompts/rcp.md`.

## Confidentialité

- L'audio et la transcription brute ne quittent jamais le Mac. Les audios traités sont rangés dans `1_Audio/_traites` : **supprimez-les** une fois le CR validé.
- Le dossier de travail est placé dans `~/EcouteIntelligente` et non dans `~/Documents`, qui peut être synchronisé avec iCloud (iCloud n'est pas un hébergeur certifié HDS).
- Les noms de fichiers sont horodatés pour ne jamais reprendre le nom de l'audio.
- Claude est appelé via `claude -p`, sans outils et sans historique de session. Variables d'environnement : `ECOUTE_MODELE` (par défaut `sonnet`), `ECOUTE_DOSSIER`, `ECOUTE_WHISPER`.

## RCP enregistrée sur l'iPhone (sans MacBook)

1. App **Dictaphone** : enregistrer toute la RCP (fonctionne écran verrouillé ; iPhone posé près des orateurs, en mode Avion ou Ne pas déranger).
2. À la fin : ouvrir l'enregistrement › **⋯ › Enregistrer dans Fichiers › iCloud Drive › EcouteIntelligente › iPhone RCP**.
3. Le Mac mini le récupère (≈ 1 min après l'arrivée dans iCloud), fait la même synthèse dossier par dossier → `4_CR_Prets`. L'audio quitte iCloud dès sa prise en charge.
4. Supprimer l'enregistrement du Dictaphone (et de « Supprimés récemment »).

Tout audio déposé dans ce dossier est traité comme une **RCP** : il n'est jamais déposé dans l'Agenda RI.

## RCP : réorganisation après coup

En RCP, le micro du MacBook capte de loin : la transcription du direct est médiocre. À la fin de l'écoute 🟢 RCP (ou à la main : `ecoute rcp fichier1.wav [fichier2.wav…]`, plusieurs morceaux recollés dans l'ordre), le son est nettoyé et remonté (ffmpeg), retranscrit avec Whisper large-v3 complet, anonymisé localement, puis Claude **réorganise dossier par dossier** (question, synthèse, décision, geste, examens, hémostase, suivi), liste les passages non compris, les actions et les brouillons de mails → `4_CR_Prets`. Pour une meilleure prise de son : micro de table ou micro de l'iPhone (`ecoute micros`).
