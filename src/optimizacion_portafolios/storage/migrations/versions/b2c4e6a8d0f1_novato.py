"""rename role explorador to novato

Only the users table changes. Historical rows of perfiles (rol_sugerido) and
the append-only registro_decisiones keep the name recorded at the time; the
code reads "explorador" as "novato" (web.auth.roles.canonical_role).

Revision ID: b2c4e6a8d0f1
Revises: f3579be64acf
Create Date: 2026-10-20 10:00:00

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'b2c4e6a8d0f1'
down_revision = 'f3579be64acf'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE users SET role = 'novato' WHERE role = 'explorador'")
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('role', server_default='novato')


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('role', server_default='explorador')
    op.execute("UPDATE users SET role = 'explorador' WHERE role = 'novato'")
