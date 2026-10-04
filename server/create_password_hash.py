from getpass import getpass
from app.core.auth import hash_password

p1 = getpass("Введите пароль профиля FulfillCore: ")
p2 = getpass("Повторите пароль: ")
if not p1:
    raise SystemExit("Пароль не может быть пустым")
if p1 != p2:
    raise SystemExit("Пароли не совпадают")
print(hash_password(p1))
