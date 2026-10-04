import { ArrowClockwise, ArrowRight, ListBullets } from '@phosphor-icons/react'
import { useEffect, useState } from 'react'
import { ApiError, api } from '../api/client.ts'
import type { TransactionView } from '../api/contracts.gen.ts'
import type { Copy } from '../i18n.ts'

interface Props {
  token: string
  copy: Copy
  onExpired: () => void
  onPick: (text: string) => void
}

// Always with its cents. The amount is a two-decimal value, and Intl's default for some
// currencies is none: 85,900.50 COP would read "85.901 COP", an amount the customer never paid
// and the agent cannot find when the question below names it.
function amountOf(transaction: TransactionView, locale: string): string {
  return new Intl.NumberFormat(locale, {
    style: 'currency',
    currency: transaction.currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(Number(transaction.amount))
}

function dateOf(transaction: TransactionView, locale: string): string {
  return new Intl.DateTimeFormat(locale, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(new Date(transaction.transaction_ts))
}

export function Transactions({ token, copy, onExpired, onPick }: Props) {
  const [transactions, setTransactions] = useState<TransactionView[] | null>(null)
  const [failed, setFailed] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [request, setRequest] = useState(0)

  useEffect(() => {
    let active = true
    api.transactions(token).then(
      (rows) => {
        if (active) {
          setTransactions(rows)
          setFailed(false)
        }
      },
      (error: unknown) => {
        if (!active) return
        if (error instanceof ApiError && error.status === 401) onExpired()
        else setFailed(true)
      },
    )
    return () => {
      active = false
    }
  }, [token, request, onExpired])

  const visible = expanded ? transactions : transactions?.slice(0, 5)

  return (
    <section className="transactions" aria-labelledby="transactions-title">
      <div className="transactions__head">
        <span className="transactions__icon" aria-hidden="true"><ListBullets /></span>
        <div>
          <h2 id="transactions-title">{copy.transactionsTitle}</h2>
          <p>{copy.transactionsLead}</p>
        </div>
      </div>
      {failed ? (
        <div className="transactions__state" role="alert">
          <span>{copy.transactionsFailed}</span>
          <button type="button" className="button button--secondary button--small" onClick={() => {
            setFailed(false)
            setRequest((current) => current + 1)
          }}>
            <ArrowClockwise aria-hidden="true" />{copy.transactionsRetry}
          </button>
        </div>
      ) : transactions === null ? (
        <output className="transactions__state">{copy.transactionsLoading}</output>
      ) : transactions.length === 0 ? (
        <p className="transactions__state">{copy.transactionsEmpty}</p>
      ) : (
        <>
          <ul className="transactions__list">
            {visible?.map((transaction) => (
              <li key={transaction.transaction_id} className="transactions__row">
                <div className="transactions__facts">
                  <strong>{transaction.merchant_name ?? transaction.transaction_id}</strong>
                  <span>{dateOf(transaction, copy.locale)}{transaction.card_last4 == null ? '' : ` · ${copy.transactionsCard(transaction.card_last4)}`}</span>
                  <span className="transactions__reference">
                    {copy.transactionsReference(transaction.transaction_id)}
                  </span>
                </div>
                <span className="transactions__amount">{amountOf(transaction, copy.locale)}</span>
                <button
                  type="button"
                  className="transactions__ask"
                  aria-label={`${copy.transactionsAsk}: ${transaction.merchant_name ?? transaction.transaction_id}, ${amountOf(transaction, copy.locale)}, ${copy.transactionsReference(transaction.transaction_id)}`}
                  title={copy.transactionsAsk}
                  onClick={() =>
                    onPick(
                      copy.transactionsQuestion(
                        transaction.transaction_id,
                        transaction.merchant_name ?? null,
                        amountOf(transaction, copy.locale),
                        dateOf(transaction, copy.locale),
                      ),
                    )
                  }
                >
                  <span>{copy.transactionsAskShort}</span>
                  <ArrowRight aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
          {!expanded && transactions.length > 5 && (
            <button type="button" className="transactions__more" onClick={() => setExpanded(true)}>
              {copy.transactionsMore}
            </button>
          )}
        </>
      )}
    </section>
  )
}
