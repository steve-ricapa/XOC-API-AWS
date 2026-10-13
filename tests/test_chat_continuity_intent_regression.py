"""Reproduce the reported continuity failure offline; no runtime/cloud writes."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.handlers.routes import chat


class ContinuityIntentRegressionTests(unittest.TestCase):
    def test_recalling_existing_ticket_is_not_creation_intent(self):
        self.assertIsNone(chat._detect_ticket_creation_intent(
            '¿Qué ticket acabamos de revisar? Responde sin consultar herramientas.'))

    def test_continuity_answer_is_preserved_without_proposal(self):
        answer = {'text': 'Revisamos el ticket de prueba existente.', 'thread_id': 'thread_fixture'}
        user = SimpleNamespace(id=12, role='ADMIN', tenant_id=7, delegation_active=False)
        with patch.object(chat, '_ticket_confirmation_token', return_value='offline-fixture'):
            result = chat._maybe_create_ticket_from_intent(
                dict(answer), '¿Qué ticket acabamos de revisar? Responde sin consultar herramientas.',
                7, user, request_id='offline-continuity')
        self.assertNotIn('ticket_proposal', result)
        self.assertEqual(answer['text'], result['text'])

    def test_explicit_creation_remains_recognized(self):
        self.assertIsNotNone(chat._detect_ticket_creation_intent('Quiero crear ticket para revisar el servidor.'))

    def test_read_history_negation_and_hypothetical_are_not_requests(self):
        for message in (
            'Revisa mis tickets', 'Analiza mis documentos', 'Tengo una alerta de malware',
            'No quiero crear un ticket', 'No crees un ticket', 'Sin crear ticket, revisa el estado',
            '¿Cómo puedo crear un ticket?', 'Si quisiera crear un ticket, ¿cómo se hace?',
            'Ayer te pedi crear ticket', 'Explica la frase crear ticket',
            'Crea un ticket. No lo crees todavia.', "Do not create a ticket", 'Check the ticket',
        ):
            with self.subTest(message=message):
                self.assertIsNone(chat._detect_ticket_creation_intent(message))

    def test_explicit_requests_and_proposals(self):
        for message in (
            'Crea un ticket para revisar la VPN', 'Quiero crear un ticket',
            '¿Puedes abrir un ticket?', 'Por favor, genera un ticket',
            'Sophia, propón un ticket de prueba', 'Necesito crear un nuevo ticket',
            'Create a ticket', 'Could you open a new ticket',
            'Crea un ticket para revisar la VPN, no ejecutes acciones',
        ):
            with self.subTest(message=message):
                self.assertIsNotNone(chat._detect_ticket_creation_intent(message))

    def test_timing_does_not_log_payload_or_exception(self):
        from unittest.mock import MagicMock
        logger = MagicMock()
        with patch.object(chat, '_latency_logger', logger), patch.object(chat.requests, 'post', side_effect=ValueError('SECRET')):
            with self.assertRaises(ValueError):
                chat._timed_sophia_post('second_turn', 'server-id', url='https://example.invalid', json={'secret': 'SECRET'})
        self.assertEqual(1, logger.info.call_count)
        self.assertNotIn('SECRET', str(logger.info.call_args))


if __name__ == '__main__':
    unittest.main()
