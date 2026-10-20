"""rename role explorador to novato

The append-only decision log (registro_decisiones) is left as recorded: its
rows keep the role name in force when each decision was made.

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
    op.execute("UPDATE perfiles SET rol_sugerido = 'novato' WHERE rol_sugerido = 'explorador'")
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('role', server_default='novato')


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('role', server_default='explorador')
    op.execute("UPDATE perfiles SET rol_sugerido = 'explorador' WHERE rol_sugerido = 'novato'")
    op.execute("UPDATE users SET role = 'explorador' WHERE role = 'novato'")
