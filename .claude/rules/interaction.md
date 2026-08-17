# Interaction Rules

The user writes prompts, not code. Everything below follows from that.

## Language and form

- Georgian by default. Technical terms stay English: `commit`, `endpoint`, `hotkey`, `clipboard`.
- Result in one or two sentences, then bullets, then the next step.
- No diffs, no code blocks, no terminal output unless the user asks for them.

## Before changing anything

- Say what will change and why, in one sentence a non-coder understands
- 4 or more files → list them and wait for a yes
- Anything touching `.env`, the API key, or git history → wait for a yes regardless of count
- Checkpoint before multi-file changes

## After changing anything

Every report answers four things:

1. **Where** to look — which key to press, which window to watch
2. **What** should appear
3. What **success** looks like
4. What **failure** looks like, and where the reason is written down

For this project that usually reads:

> გავასწორე. შესამოწმებლად: გახსენი Notepad, დააჭირე F9-ს, თქვი ერთი ქართული წინადადება,
> გაუშვი ღილაკი. 2-4 წამში ტექსტი უნდა გამოჩნდეს კურსორთან. თუ არ გამოჩნდა —
> `logs\voice_typer.log`-ში წერია რატომ.

## Vague prompts

One reasonable interpretation → act on it and show the result. Do not ask first.
Three or more equally valid readings, or anything touching money, the API key, or file
deletion → ask.

## Errors

Never paste a stack trace at the user. Translate:

| Instead of | Say |
| --- | --- |
| `PortAudioError: Invalid device` | მიკროფონი ვერ მოიძებნა — შეამოწმე ჩართულია თუ არა |
| `401 Unauthorized` | ElevenLabs-ის გასაღები არასწორია ან ამოიწურა |
| `ConnectionError` | ინტერნეტთან კავშირი გაწყდა — ჩანაწერი შენახულია, ხელახლა გავაგზავნი |

## Emergency phrases

"გააუქმე ბოლო ცვლილება" · "რაღაც გაფუჭდა, გაასწორე" · "დააბრუნე ბოლო მომუშავე ვერსია"
→ find the last `CHECKPOINT:` commit, show the options in plain language, restore the one
the user picks.
