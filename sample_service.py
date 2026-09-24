class PaymentService:
    def charge(self, amount: float, card_token: str):
        # Migrated to RetryExecutor
        return self.retry_executor.execute(lambda: self.client.post("/charges", {"amount": amount}))
