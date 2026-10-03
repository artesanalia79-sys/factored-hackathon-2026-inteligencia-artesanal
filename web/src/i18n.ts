// UI chrome in Spanish and Portuguese: labels, buttons, hints and status names. What the agent says
// is never written here: it comes from the backend templates (docs/rules/web.md).
import type { ActionType, Language } from './api/contracts.gen.ts'

export interface Copy {
  locale: string
  product: string
  demoNotice: string
  languageName: Record<Language, string>
  chooseLanguage: string
  // login
  loginTitle: string
  loginLead: string
  promises: readonly string[]
  personasLegend: string
  personasLoading: string
  personasFailed: string
  personaUnavailable: string
  accessCodeLabel: string
  accessCodeHint: string
  accessCodeRequired: string
  accessCodeWrong: string
  retry: string
  sendCode: string
  sendingCode: string
  codeTitle: (name: string) => string
  codeLead: string
  codeLeadNoDemo: string
  codeLabel: string
  demoCode: string
  useCode: string
  signIn: string
  signingIn: string
  otherPersona: string
  invalidCode: string
  tooManyAttempts: (seconds: number) => string
  networkError: string
  sessionEndedNotice: string
  credit: string
  // chat
  sessionUntil: (time: string) => string
  signOut: string
  greeting: (name: string) => string
  greetingLead: string
  tryLabel: string
  tryAgainLabel: string
  conversationLabel: string
  messagesLabel: string
  you: string
  agent: string
  typing: string
  composerLabel: string
  composerHint: string
  send: string
  sendFailed: string
  ended: string
  sessionExpired: string
  signInAgain: string
  verified: Record<ActionType, string>
  // confirmation panel
  confirmTitle: string
  confirmActions: Record<'create_dispute' | 'block_card', string>
  fields: {
    merchant: string
    amount: string
    date: string
    card: string
    channel: string
    reason: string
  }
  cardEnding: (last4: string) => string
  confirmHint: string
  confirm: string
  cancel: string
  // what the panel's buttons send: an answer the agent reads like a typed one
  confirmWords: string
  cancelWords: string
}

const es: Copy = {
  locale: 'es',
  product: 'Asistente de reclamos',
  demoNotice: 'Demo con clientes y movimientos sintéticos',
  languageName: { es: 'Español', pt: 'Português' },
  chooseLanguage: 'Idioma',
  loginTitle: 'Revisa un cargo de tu tarjeta',
  loginLead:
    'Un cargo que no reconoces, un cobro duplicado o una compra que no llegó. El asistente te muestra el movimiento y tú decides.',
  promises: [
    'Nada cambia en tu cuenta sin tu confirmación.',
    'Solo te mostramos datos leídos de tu cuenta.',
    'Si tu caso lo necesita, pasa a una persona del equipo.',
  ],
  personasLegend: 'Elige un cliente de prueba',
  personasLoading: 'Cargando clientes de prueba',
  personasFailed: 'No pudimos cargar los clientes de prueba.',
  personaUnavailable: 'Ese cliente ya no está disponible. Elige uno de la lista actualizada.',
  accessCodeLabel: 'Código de acceso de la demo',
  accessCodeHint: 'Esta demo es privada: el equipo te comparte el código.',
  accessCodeRequired: 'Esta demo pide un código de acceso. Escríbelo y vuelve a enviar.',
  accessCodeWrong: 'El código de acceso no es correcto.',
  retry: 'Reintentar',
  sendCode: 'Enviar código',
  sendingCode: 'Enviando código',
  codeTitle: (name) => `Código de acceso de ${name}`,
  codeLead: 'En esta demo no hay SMS: el código aparece aquí abajo.',
  codeLeadNoDemo: 'Ingresa el código que recibiste.',
  codeLabel: 'Código de 6 dígitos',
  demoCode: 'Código de la demo',
  useCode: 'Usar código',
  signIn: 'Entrar',
  signingIn: 'Verificando',
  otherPersona: 'Elegir otro cliente',
  invalidCode:
    'El código no es válido o ya venció. Revísalo, o elige el cliente de nuevo para recibir otro.',
  tooManyAttempts: (seconds) =>
    seconds > 0
      ? `Demasiados intentos. Vuelve a intentarlo en ${Math.ceil(seconds / 60)} min.`
      : 'Demasiados intentos con este código. Elige el cliente de nuevo para recibir otro.',
  networkError: 'No pudimos conectar con el servicio. Inténtalo de nuevo.',
  sessionEndedNotice: 'Tu sesión terminó. Entra de nuevo para continuar.',
  credit: 'Prototipo de Inteligencia Artesanal para el Factored AI & Data Hackathon 2026.',
  sessionUntil: (time) => `Sesión hasta las ${time}`,
  signOut: 'Cerrar sesión',
  greeting: (name) => `Hola, ${name}`,
  greetingLead: 'Cuéntale al asistente qué pasó con el movimiento que quieres revisar.',
  tryLabel: 'Ejemplos para la demo',
  tryAgainLabel: 'Prueba otra consulta',
  conversationLabel: 'Conversación',
  messagesLabel: 'Mensajes',
  you: 'Tú',
  agent: 'Asistente',
  typing: 'El asistente está escribiendo',
  composerLabel: 'Tu mensaje',
  composerHint: 'Enter para enviar, Mayús + Enter para una línea nueva.',
  send: 'Enviar',
  sendFailed:
    'No pudimos confirmar si tu mensaje llegó. Revisa la conversación antes de enviarlo de nuevo.',
  ended: 'Conversación finalizada',
  sessionExpired: 'Tu sesión terminó. Entra de nuevo para continuar.',
  signInAgain: 'Entrar de nuevo',
  verified: {
    create_dispute: 'Reclamo registrado y verificado',
    block_card: 'Bloqueo de tarjeta verificado',
    create_handoff: 'Caso enviado a una persona del equipo',
  },
  confirmTitle: 'Confirma antes de continuar',
  confirmActions: { create_dispute: 'Crear un reclamo', block_card: 'Bloquear la tarjeta' },
  fields: {
    merchant: 'Comercio',
    amount: 'Importe',
    date: 'Fecha',
    card: 'Tarjeta',
    channel: 'Canal',
    reason: 'Motivo',
  },
  cardEnding: (last4) => `terminada en ${last4}`,
  confirmHint: 'Nada cambia hasta que confirmes. También puedes responder por escrito.',
  confirm: 'Confirmar',
  cancel: 'Cancelar',
  confirmWords: 'Sí, confirmo',
  cancelWords: 'No',
}

const pt: Copy = {
  locale: 'pt-BR',
  product: 'Assistente de contestações',
  demoNotice: 'Demo com clientes e transações sintéticos',
  languageName: { es: 'Español', pt: 'Português' },
  chooseLanguage: 'Idioma',
  loginTitle: 'Analise uma cobrança do seu cartão',
  loginLead:
    'Uma cobrança que você não reconhece, uma cobrança duplicada ou uma compra que não chegou. O assistente mostra a transação e você decide.',
  promises: [
    'Nada muda na sua conta sem a sua confirmação.',
    'Mostramos apenas dados lidos da sua conta.',
    'Se o seu caso precisar, ele passa para uma pessoa da equipe.',
  ],
  personasLegend: 'Escolha um cliente de teste',
  personasLoading: 'Carregando clientes de teste',
  personasFailed: 'Não conseguimos carregar os clientes de teste.',
  personaUnavailable: 'Esse cliente não está mais disponível. Escolha um da lista atualizada.',
  accessCodeLabel: 'Código de acesso da demo',
  accessCodeHint: 'Esta demo é privada: a equipe compartilha o código com você.',
  accessCodeRequired: 'Esta demo pede um código de acesso. Digite-o e envie de novo.',
  accessCodeWrong: 'O código de acesso não está correto.',
  retry: 'Tentar de novo',
  sendCode: 'Enviar código',
  sendingCode: 'Enviando código',
  codeTitle: (name) => `Código de acesso de ${name}`,
  codeLead: 'Nesta demo não há SMS: o código aparece aqui embaixo.',
  codeLeadNoDemo: 'Digite o código que você recebeu.',
  codeLabel: 'Código de 6 dígitos',
  demoCode: 'Código da demo',
  useCode: 'Usar código',
  signIn: 'Entrar',
  signingIn: 'Verificando',
  otherPersona: 'Escolher outro cliente',
  invalidCode:
    'O código não é válido ou já expirou. Confira, ou escolha o cliente de novo para receber outro.',
  tooManyAttempts: (seconds) =>
    seconds > 0
      ? `Muitas tentativas. Tente de novo em ${Math.ceil(seconds / 60)} min.`
      : 'Muitas tentativas com este código. Escolha o cliente de novo para receber outro.',
  networkError: 'Não conseguimos conectar ao serviço. Tente de novo.',
  sessionEndedNotice: 'Sua sessão terminou. Entre de novo para continuar.',
  credit: 'Protótipo da Inteligencia Artesanal para o Factored AI & Data Hackathon 2026.',
  sessionUntil: (time) => `Sessão até ${time}`,
  signOut: 'Sair',
  greeting: (name) => `Olá, ${name}`,
  greetingLead: 'Conte ao assistente o que aconteceu com a transação que você quer analisar.',
  tryLabel: 'Exemplos para a demo',
  tryAgainLabel: 'Tente outra consulta',
  conversationLabel: 'Conversa',
  messagesLabel: 'Mensagens',
  you: 'Você',
  agent: 'Assistente',
  typing: 'O assistente está digitando',
  composerLabel: 'Sua mensagem',
  composerHint: 'Enter para enviar, Shift + Enter para uma nova linha.',
  send: 'Enviar',
  sendFailed:
    'Não conseguimos confirmar se a sua mensagem chegou. Confira a conversa antes de enviá-la de novo.',
  ended: 'Conversa encerrada',
  sessionExpired: 'Sua sessão terminou. Entre de novo para continuar.',
  signInAgain: 'Entrar de novo',
  verified: {
    create_dispute: 'Contestação registrada e verificada',
    block_card: 'Bloqueio do cartão verificado',
    create_handoff: 'Caso enviado para uma pessoa da equipe',
  },
  confirmTitle: 'Confirme antes de continuar',
  confirmActions: { create_dispute: 'Abrir uma contestação', block_card: 'Bloquear o cartão' },
  fields: {
    merchant: 'Estabelecimento',
    amount: 'Valor',
    date: 'Data',
    card: 'Cartão',
    channel: 'Canal',
    reason: 'Motivo',
  },
  cardEnding: (last4) => `com final ${last4}`,
  confirmHint: 'Nada muda até você confirmar. Você também pode responder por escrito.',
  confirm: 'Confirmar',
  cancel: 'Cancelar',
  confirmWords: 'Sim, confirmo',
  cancelWords: 'Não',
}

export const COPY: Record<Language, Copy> = { es, pt }

export function browserLanguage(): Language {
  return navigator.language.toLowerCase().startsWith('pt') ? 'pt' : 'es'
}

export function countryName(code: string, copy: Copy): string {
  try {
    return new Intl.DisplayNames([copy.locale], { type: 'region' }).of(code) ?? code
  } catch {
    return code
  }
}

export function clockTime(iso: string, copy: Copy): string {
  return new Intl.DateTimeFormat(copy.locale, { hour: '2-digit', minute: '2-digit' }).format(
    new Date(iso),
  )
}
