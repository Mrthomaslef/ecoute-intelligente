<img src="assets/icon.svg" width="64" height="64" alt="">

# Écoute intelligente

Enregistrement de consultation ou de RCP → **transcription 100 % locale** (Whisper sur puce Apple) → **anonymisation locale** → **relecture humaine obligatoire** → rédaction du CR, des actions et des brouillons de mails par Claude.

> [!WARNING]
> Rien ne part vers Claude tant que **vous** n'avez pas relu la note et ne l'avez pas déplacée dans `3_Valide`. L'anonymisation automatique masque les noms précédés d'une civilité, les dates complètes, les IPP/NIP/INS (même dictés en lettres), les téléphones et les e-mails, mais **elle n'est pas infaillible** : un nom prononcé seul (« le dossier Lebrun ») est seulement *signalé*, pas masqué.

## Installation (MacBook Air ou Mac mini, Apple Silicon)

```bash
git clone https://github.com/mrthomaslef/ecoute-intelligente.git ~/ecoute-intelligente
cd ~/ecoute-intelligente && ./install.sh
```

Le script installe ffmpeg, Whisper (MLX), précharge une fois le modèle `large-v3-turbo` (≈1,6 Go) et active en arrière-plan la surveillance automatique des dossiers `~/EcouteIntelligente`. Ensuite, tout fonctionne hors ligne, sauf l'étape Claude.

## En consultation : écoute en direct

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
