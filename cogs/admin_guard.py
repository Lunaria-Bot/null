from discord import app_commands

# IDs des rôles staff autorisés
STAFF_ROLE_IDS = [1342461081133518949, 1304102244462886982]

def is_staff():
    """
    Vérifie si l'utilisateur qui exécute la commande
    possède au moins un des rôles staff définis ci-dessus.
    """
    async def predicate(interaction):
        return any(role.id in STAFF_ROLE_IDS for role in interaction.user.roles)
    return app_commands.check(predicate)
