class ServiceError(Exception):
    pass


class NotFound(ServiceError):
    pass


class NoEligibleLeg(ServiceError):
    pass


class InsufficientFunds(ServiceError):
    pass


class TooManyActiveTasks(ServiceError):
    pass


class PaymentConflict(ServiceError):
    pass
