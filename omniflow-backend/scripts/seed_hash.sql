UPDATE tenant_users SET hashed_password = '$2b$12$ivEfJtCwy5uwsTctXos94enVqZgbwLMn689ljK5A5HhNbhE9t8mY2';
SELECT email, LEFT(hashed_password, 20) as hash_prefix FROM tenant_users;
