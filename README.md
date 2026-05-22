# ZooStore

Веб-сайт зоомагазина "Любимый хвостик" на Flask с каталогом, объявлениями, админкой и ссылкой на Telegram-профиль магазина.

## Локальный запуск

```bash
cd ~/PyCharmMiscProject/zoo_store
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python app.py
```

Админка:

```text
http://127.0.0.1:5000/admin
login: admin
password: admin123
```

Пароль можно поменять через переменную `ADMIN_PASSWORD` до первого запуска проекта.

## Деплой на VPS через rsync

```bash
rsync -avz --exclude 'venv/' --exclude '__pycache__/' --exclude '.env' --exclude 'instance/*.db' ~/PyCharmMiscProject/zoo_store/ user@37.233.83.18:/home/user/siteflask/
```

На сервере:

```bash
cd ~/siteflask
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart lubimy-hvostik
sudo systemctl status lubimy-hvostik --no-pager
```

Проверка:

```bash
curl -I https://любимый-хвостик.рф
```

## Telegram

В `.env` нужно указать ссылку на Telegram-профиль, куда будут вести кнопки связи на сайте:

```text
TELEGRAM_URL=https://t.me/username
```

## Источники примерных изображений

- Cat eating from a bowl: https://commons.wikimedia.org/wiki/File:Cat_eating_from_a_bowl.jpg
- Dog with toys: https://commons.wikimedia.org/wiki/File:Dog_with_toys.jpg
- Scratching post: https://commons.wikimedia.org/wiki/File:Scratching_post.jpg
- Dog on bed: https://commons.wikimedia.org/wiki/File:Dog_on_bed_(31067596195).jpg
