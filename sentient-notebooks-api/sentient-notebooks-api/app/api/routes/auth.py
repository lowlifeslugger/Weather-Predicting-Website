from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.core.security import create_access_token, hash_password, verify_password
from app.db.models import Notebook, User
from app.schemas.user import Token, UserCreate, UserOut
from app.services.quota import get_user_storage_usage

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(user_in: UserCreate, db: Session = Depends(get_db)):
    user_count = db.query(User).count()
    if user_count >= settings.max_users:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Registration is closed -- this instance is at capacity ({settings.max_users} accounts).",
        )

    existing = db.query(User).filter(User.email == user_in.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(email=user_in.email, hashed_password=hash_password(user_in.password))
    db.add(user)
    db.commit()
    db.refresh(user)

    # Every account gets exactly one notebook, created automatically --
    # there's no "create a notebook" step in the product, so the frontend
    # never needs to show one.
    notebook = Notebook(
        user_id=user.id,
        name="My Notebook",
        llm_model=settings.default_llm_model,
    )
    db.add(notebook)
    db.commit()

    return user


@router.post("/login", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    # OAuth2PasswordRequestForm uses "username" as the field name -- we treat it as email.
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token(subject=str(user.id))
    return Token(access_token=token)


@router.get("/me", response_model=UserOut)
def read_current_user(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    used = get_user_storage_usage(db, current_user.id)
    return UserOut(
        id=current_user.id,
        email=current_user.email,
        storage_used_bytes=used,
        storage_limit_bytes=settings.max_storage_bytes_per_user,
    )
